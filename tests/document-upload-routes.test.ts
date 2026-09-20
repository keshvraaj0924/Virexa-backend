import Fastify from 'fastify'
import cookie from '@fastify/cookie'
import { describe, expect, it, vi } from 'vitest'
import type { AuthRepository } from '../src/auth/repository.js'
import { AuthenticationRequiredError, PermissionDeniedError } from '../src/auth/context.js'
import type { AuditService } from '../src/audit/service.js'
import { apiFailure } from '../src/contracts/http.js'
import type { DocumentRecord } from '../src/contracts/documents.js'
import type { DocumentRepository } from '../src/documents/repository.js'
import { registerDocumentRoutes } from '../src/documents/routes.js'
import type { DocumentUploadService } from '../src/documents/upload-service.js'

const organizationId = '11111111-1111-4111-8111-111111111111'
const userId = '22222222-2222-4222-8222-222222222222'
const documentId = '33333333-3333-4333-8333-333333333333'
const attemptId = '55555555-5555-4555-8555-555555555555'
const trustedOrigin = 'https://app.virexa.test'

const document: DocumentRecord = {
  id: documentId,
  organizationId,
  branchId: null,
  departmentId: null,
  source: 'upload',
  externalReference: null,
  originalFileName: 'invoice.pdf',
  mediaType: 'application/pdf',
  sizeBytes: 1024,
  checksumSha256: 'a'.repeat(64),
  status: 'received',
  failureCode: null,
  receivedAt: '2026-09-20T12:00:00.000Z',
  updatedAt: '2026-09-20T12:00:00.000Z',
}

const attempt = {
  id: attemptId,
  organizationId,
  documentId,
  idempotencyKey: 'upload-1',
  objectKey: `organizations/${organizationId}/documents/${documentId}`,
  status: 'initiated' as const,
  createdAt: '2026-09-20T12:01:00.000Z',
  completedAt: null,
  updatedAt: '2026-09-20T12:01:00.000Z',
  failureCode: null,
}

function authRepository(role: 'operator' | 'viewer' = 'operator'): AuthRepository {
  return {
    register: vi.fn(), login: vi.fn(), listActiveSessions: vi.fn(), revokeSession: vi.fn(),
    revokeOwnedSession: vi.fn(), revokeOtherSessions: vi.fn(), ping: vi.fn(),
    getSession: vi.fn(async (token: string) => token === 'valid-token' ? {
      user: { id: userId, email: 'user@example.test', displayName: 'User', role, organizationId, organizationName: 'Acme' },
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    } : null),
  } as AuthRepository
}

async function build(options: { role?: 'operator' | 'viewer'; storage?: boolean } = {}) {
  process.env.FRONTEND_ORIGIN = trustedOrigin
  const app = Fastify()
  await app.register(cookie)
  const getById = vi.fn(async (tenantId: string, id: string) => tenantId === organizationId && id === documentId ? document : null)
  const documentRepository = { create: vi.fn(), list: vi.fn(), getById } as unknown as DocumentRepository
  const record = vi.fn(async () => undefined)
  const auditService = { record } as unknown as AuditService
  const initiatePersisted = vi.fn(async () => ({
    attempt,
    objectKey: attempt.objectKey,
    target: {
      uploadUrl: 'https://storage.example.test/upload',
      expiresAt: '2026-09-20T12:06:00.000Z',
      requiredHeaders: { 'content-type': document.mediaType },
    },
    replayed: false,
  }))
  const completePersisted = vi.fn(async () => ({
    attempt: { ...attempt, status: 'completed' as const, completedAt: '2026-09-20T12:03:00.000Z', updatedAt: '2026-09-20T12:03:00.000Z' },
    storedObject: { objectKey: attempt.objectKey, mediaType: document.mediaType, sizeBytes: document.sizeBytes, checksumSha256: document.checksumSha256 },
    replayed: false,
  }))
  const uploadService = options.storage === false ? undefined : { initiatePersisted, completePersisted } as unknown as DocumentUploadService

  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
    if (error instanceof PermissionDeniedError) return reply.code(403).send(apiFailure('FORBIDDEN', error.message, request.id))
    if (error.name === 'UNTRUSTED_ORIGIN') return reply.code(403).send(apiFailure('UNTRUSTED_ORIGIN', error.message, request.id))
    throw error
  })
  await registerDocumentRoutes(app, { authRepository: authRepository(options.role), auditService, documentRepository, uploadService })
  return { app, getById, record, initiatePersisted, completePersisted }
}

const headers = { cookie: 'virexa_session=valid-token', origin: trustedOrigin, 'idempotency-key': 'upload-1' }

describe('document upload HTTP boundary', () => {
  it('fails closed when durable binary storage is not configured', async () => {
    const { app, getById } = await build({ storage: false })
    const response = await app.inject({ method: 'POST', url: `/api/v1/documents/${documentId}/uploads`, headers })
    expect(response.statusCode).toBe(503)
    expect(response.json().error.code).toBe('DOCUMENT_STORAGE_UNAVAILABLE')
    expect(getById).not.toHaveBeenCalled()
    await app.close()
  })

  it('requires document:create before resolving tenant document or initiating storage', async () => {
    const { app, getById, initiatePersisted } = await build({ role: 'viewer' })
    const response = await app.inject({ method: 'POST', url: `/api/v1/documents/${documentId}/uploads`, headers })
    expect(response.statusCode).toBe(403)
    expect(getById).not.toHaveBeenCalled()
    expect(initiatePersisted).not.toHaveBeenCalled()
    await app.close()
  })

  it('validates the idempotency key before tenant-scoped document lookup', async () => {
    const { app, getById, initiatePersisted } = await build()
    const response = await app.inject({
      method: 'POST',
      url: `/api/v1/documents/${documentId}/uploads`,
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin },
    })
    expect(response.statusCode).toBe(400)
    expect(response.json().error.code).toBe('VALIDATION_ERROR')
    expect(getById).not.toHaveBeenCalled()
    expect(initiatePersisted).not.toHaveBeenCalled()
    await app.close()
  })

  it('initiates using only the authenticated organization and returns no storage internals', async () => {
    const { app, getById, initiatePersisted } = await build()
    const response = await app.inject({ method: 'POST', url: `/api/v1/documents/${documentId}/uploads`, headers })
    expect(response.statusCode).toBe(200)
    expect(getById).toHaveBeenCalledWith(organizationId, documentId)
    expect(initiatePersisted).toHaveBeenCalledWith(expect.objectContaining({ organizationId, documentId }), 'upload-1')
    const body = response.json().data
    expect(body.attempt.organizationId).toBeUndefined()
    expect(body.attempt.idempotencyKey).toBeUndefined()
    expect(body.attempt.objectKey).toBeUndefined()
    expect(body.target.uploadUrl).toMatch(/^https:\/\//)
    expect(response.headers['cache-control']).toContain('no-store')
    await app.close()
  })

  it('completes a tenant-bound attempt and audits the successful transition', async () => {
    const { app, completePersisted, record } = await build()
    const response = await app.inject({
      method: 'POST',
      url: `/api/v1/documents/${documentId}/uploads/${attemptId}/complete`,
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin },
    })
    expect(response.statusCode).toBe(200)
    expect(completePersisted).toHaveBeenCalledWith(expect.objectContaining({ organizationId, documentId }), attemptId)
    expect(record).toHaveBeenCalledWith(expect.objectContaining({
      organizationId,
      actorUserId: userId,
      action: 'document.upload_completed',
      resourceId: documentId,
      metadata: { uploadAttemptId: attemptId, replayed: false },
    }))
    await app.close()
  })
})
