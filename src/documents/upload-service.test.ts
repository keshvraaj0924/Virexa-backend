import { describe, expect, it, vi } from 'vitest'
import { DocumentUploadService } from './upload-service.js'
import type { DocumentObjectStorageLifecycle, DocumentUploadDescriptor } from './storage.js'
import type {
  CreateDocumentUploadAttempt,
  DocumentUploadAttempt,
  DocumentUploadAttemptRepository,
} from './upload-attempt-repository.js'

const descriptor: DocumentUploadDescriptor = {
  organizationId: '11111111-1111-4111-8111-111111111111',
  documentId: '22222222-2222-4222-8222-222222222222',
  originalFileName: 'invoice.pdf',
  mediaType: 'application/pdf',
  sizeBytes: 1024,
  checksumSha256: 'a'.repeat(64),
}

const nowMs = Date.parse('2026-09-20T00:00:00.000Z')

function storage(): DocumentObjectStorageLifecycle {
  return {
    async createUploadTarget(received, objectKey) {
      return {
        objectKey,
        uploadUrl: 'https://storage.example/upload',
        expiresAt: new Date(nowMs + 60_000).toISOString(),
        requiredHeaders: {
          'content-type': received.mediaType,
          'content-length': String(received.sizeBytes),
          'x-virexa-sha256': received.checksumSha256,
        },
      }
    },
    async inspectObject(objectKey) {
      return { objectKey, mediaType: descriptor.mediaType, sizeBytes: descriptor.sizeBytes, checksumSha256: descriptor.checksumSha256 }
    },
    async deleteObject() {},
  }
}

function attemptRepository(): DocumentUploadAttemptRepository {
  const byKey = new Map<string, DocumentUploadAttempt>()
  const save = (attempt: DocumentUploadAttempt) => {
    byKey.set(`${attempt.organizationId}:${attempt.idempotencyKey}`, attempt)
    return attempt
  }
  const findById = (organizationId: string, attemptId: string) =>
    [...byKey.values()].find((attempt) => attempt.organizationId === organizationId && attempt.id === attemptId) ?? null

  return {
    async createOrGet(organizationId: string, input: CreateDocumentUploadAttempt) {
      const key = `${organizationId}:${input.idempotencyKey.trim()}`
      const existing = byKey.get(key)
      if (existing) return existing
      return save({
        id: '33333333-3333-4333-8333-333333333333',
        organizationId,
        documentId: input.documentId,
        idempotencyKey: input.idempotencyKey.trim(),
        objectKey: input.objectKey,
        status: 'initiated',
        failureCode: null,
        createdAt: new Date(nowMs).toISOString(),
        completedAt: null,
        updatedAt: new Date(nowMs).toISOString(),
      })
    },
    async getById(organizationId, attemptId) {
      return findById(organizationId, attemptId)
    },
    async getByIdempotencyKey(organizationId, idempotencyKey) {
      return byKey.get(`${organizationId}:${idempotencyKey.trim()}`) ?? null
    },
    async complete(organizationId, attemptId) {
      const attempt = findById(organizationId, attemptId)
      if (!attempt || attempt.status !== 'initiated') return attempt
      return save({
        ...attempt,
        status: 'completed',
        completedAt: new Date(nowMs + 1_000).toISOString(),
        updatedAt: new Date(nowMs + 1_000).toISOString(),
      })
    },
    async fail(organizationId, attemptId, failureCode) {
      const attempt = findById(organizationId, attemptId)
      if (!attempt || attempt.status !== 'initiated') return attempt
      return save({
        ...attempt,
        status: 'failed',
        failureCode,
        updatedAt: new Date(nowMs + 1_000).toISOString(),
      })
    },
  }
}

describe('DocumentUploadService', () => {
  it('generates the object key server-side and binds it into the provider target', async () => {
    const provider = storage()
    const createTarget = vi.spyOn(provider, 'createUploadTarget')
    const initiated = await new DocumentUploadService(provider).initiate(descriptor, nowMs)
    expect(initiated.objectKey).toMatch(/^documents\/[0-9a-f]{32}\/22222222-2222-4222-8222-222222222222\/[0-9a-f-]+$/)
    expect(createTarget).toHaveBeenCalledWith(descriptor, initiated.objectKey)
    expect(initiated.target.objectKey).toBe(initiated.objectKey)
  })

  it('reuses the persisted trusted object key when an idempotent initiation is replayed', async () => {
    const provider = storage()
    const createTarget = vi.spyOn(provider, 'createUploadTarget')
    const service = new DocumentUploadService(provider, attemptRepository())

    const first = await service.initiatePersisted(descriptor, ' upload-request-1 ', nowMs)
    const replay = await service.initiatePersisted(descriptor, 'upload-request-1', nowMs)

    expect(first.replayed).toBe(false)
    expect(replay.replayed).toBe(true)
    expect(replay.attempt.id).toBe(first.attempt.id)
    expect(replay.objectKey).toBe(first.objectKey)
    expect(createTarget).toHaveBeenLastCalledWith(descriptor, first.objectKey)
  })

  it('rejects idempotency replay when the key is already bound to another document', async () => {
    const repository = attemptRepository()
    const service = new DocumentUploadService(storage(), repository)
    await service.initiatePersisted(descriptor, 'upload-request-2', nowMs)

    await expect(service.initiatePersisted({
      ...descriptor,
      documentId: '44444444-4444-4444-8444-444444444444',
    }, 'upload-request-2', nowMs)).rejects.toThrow(/different document/)
  })

  it('persists completion only after provider-observed integrity verification', async () => {
    const repository = attemptRepository()
    const complete = vi.spyOn(repository, 'complete')
    const service = new DocumentUploadService(storage(), repository)
    const initiated = await service.initiatePersisted(descriptor, 'upload-request-3', nowMs)

    const completed = await service.completePersisted(descriptor, initiated.attempt.id)

    expect(completed.replayed).toBe(false)
    expect(completed.attempt.status).toBe('completed')
    expect(completed.storedObject.objectKey).toBe(initiated.objectKey)
    expect(complete).toHaveBeenCalledWith(descriptor.organizationId, initiated.attempt.id)
  })

  it('replays completed requests without rewriting terminal state', async () => {
    const repository = attemptRepository()
    const complete = vi.spyOn(repository, 'complete')
    const service = new DocumentUploadService(storage(), repository)
    const initiated = await service.initiatePersisted(descriptor, 'upload-request-4', nowMs)
    await service.completePersisted(descriptor, initiated.attempt.id)

    const replay = await service.completePersisted(descriptor, initiated.attempt.id)

    expect(replay.replayed).toBe(true)
    expect(replay.attempt.status).toBe('completed')
    expect(complete).toHaveBeenCalledTimes(1)
  })

  it('persists a terminal failure when durable metadata violates integrity', async () => {
    const provider = storage()
    provider.inspectObject = async (objectKey) => ({
      objectKey,
      mediaType: descriptor.mediaType,
      sizeBytes: descriptor.sizeBytes + 1,
      checksumSha256: descriptor.checksumSha256,
    })
    const repository = attemptRepository()
    const fail = vi.spyOn(repository, 'fail')
    const service = new DocumentUploadService(provider, repository)
    const initiated = await service.initiatePersisted(descriptor, 'upload-request-5', nowMs)

    await expect(service.completePersisted(descriptor, initiated.attempt.id)).rejects.toThrow(/content length/)
    expect(fail).toHaveBeenCalledWith(
      descriptor.organizationId,
      initiated.attempt.id,
      'object_integrity_verification_failed',
    )
    await expect(repository.getById(descriptor.organizationId, initiated.attempt.id)).resolves.toMatchObject({ status: 'failed' })
  })

  it('does not resolve an upload attempt through another organization scope', async () => {
    const repository = attemptRepository()
    const service = new DocumentUploadService(storage(), repository)
    const initiated = await service.initiatePersisted(descriptor, 'upload-request-6', nowMs)

    await expect(service.completePersisted({
      ...descriptor,
      organizationId: '55555555-5555-4555-8555-555555555555',
    }, initiated.attempt.id)).rejects.toThrow(/not found/)
  })

  it('verifies completion from provider-observed metadata', async () => {
    const provider = storage()
    const service = new DocumentUploadService(provider)
    const initiated = await service.initiate(descriptor, nowMs)
    await expect(service.verifyCompletion(descriptor, initiated.objectKey)).resolves.toMatchObject({ objectKey: initiated.objectKey })
  })

  it('fails closed and attempts cleanup when durable metadata violates integrity', async () => {
    const provider = storage()
    const deleteObject = vi.spyOn(provider, 'deleteObject')
    provider.inspectObject = async (objectKey) => ({ objectKey, mediaType: descriptor.mediaType, sizeBytes: 2048, checksumSha256: descriptor.checksumSha256 })
    const service = new DocumentUploadService(provider)
    const initiated = await service.initiate(descriptor, nowMs)
    await expect(service.verifyCompletion(descriptor, initiated.objectKey)).rejects.toThrow(/content length/)
    expect(deleteObject).toHaveBeenCalledWith(initiated.objectKey)
  })
})
