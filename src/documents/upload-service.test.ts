import { describe, expect, it, vi } from 'vitest'
import { DocumentUploadService } from './upload-service.js'
import type { DocumentObjectStorageLifecycle, DocumentUploadDescriptor } from './storage.js'

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

describe('DocumentUploadService', () => {
  it('generates the object key server-side and binds it into the provider target', async () => {
    const provider = storage()
    const createTarget = vi.spyOn(provider, 'createUploadTarget')
    const initiated = await new DocumentUploadService(provider).initiate(descriptor, nowMs)
    expect(initiated.objectKey).toMatch(/^documents\/[0-9a-f]{32}\/22222222-2222-4222-8222-222222222222\/[0-9a-f-]+$/)
    expect(createTarget).toHaveBeenCalledWith(descriptor, initiated.objectKey)
    expect(initiated.target.objectKey).toBe(initiated.objectKey)
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
