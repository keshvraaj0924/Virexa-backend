import Fastify from 'fastify'
import cookie from '@fastify/cookie'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AuthRepository } from '../src/auth/repository.js'
import { AuthenticationRequiredError, PermissionDeniedError } from '../src/auth/context.js'
import type { AuditService } from '../src/audit/service.js'
import { apiFailure } from '../src/contracts/http.js'
import type { DocumentExtraction } from '../src/contracts/extractions.js'
import type { DocumentExtractionRepository } from '../src/extractions/repository.js'
import { registerExtractionRoutes } from '../src/extractions/routes.js'

const organizationId = '11111111-1111-4111-8111-111111111111'
const userId = '22222222-2222-4222-8222-222222222222'
const documentId = '33333333-3333-4333-8333-333333333333'
const extractionId = '44444444-4444-4444-8444-444444444444'
const otherOrganizationId = '55555555-5555-4555-8555-555555555555'
const trustedOrigin = 'https://app.virexa.test'

const extraction: DocumentExtraction = {
  id: extractionId,
  documentId,
  status: 'queued',
  schemaVersion: 'invoice-v1',
  fields: [],
  failureCode: null,
  createdAt: '2026-09-22T00:00:00.000Z',
  updatedAt: '2026-09-22T00:00:00.000Z',
  completedAt: null,
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

async function build(role: 'operator' | 'viewer' = 'operator') {
  const app = Fastify()
  await app.register(cookie)
  const list = vi.fn(async () => ({ items: [extraction], nextCursor: null }))
  const createOrReplay = vi.fn(async () => extraction)
  const record = vi.fn(async () => undefined)
  const extractionRepository = { list, createOrReplay } as unknown as DocumentExtractionRepository
  const auditService = { record } as unknown as AuditService
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
    if (error instanceof PermissionDeniedError) return reply.code(403).send(apiFailure('FORBIDDEN', error.message, request.id))
    if (error.name === 'UNTRUSTED_ORIGIN') return reply.code(403).send(apiFailure('UNTRUSTED_ORIGIN', error.message, request.id))
    throw error
  })
  await registerExtractionRoutes(app, { authRepository: authRepository(role), auditService, extractionRepository })
  return { app, list, createOrReplay, record }
}

afterEach(() => { delete process.env.FRONTEND_ORIGIN })

describe('extraction HTTP boundary', () => {
  it('requires authentication before extraction repository access', async () => {
    const { app, list } = await build()
    const response = await app.inject({ method: 'GET', url: `/api/v1/documents/${documentId}/extractions` })
    expect(response.statusCode).toBe(401)
    expect(list).not.toHaveBeenCalled()
    await app.close()
  })

  it('derives tenant scope from the authenticated session and rejects tenant selectors', async () => {
    const { app, list } = await build()
    const headers = { cookie: 'virexa_session=valid-token' }
    const response = await app.inject({ method: 'GET', url: `/api/v1/documents/${documentId}/extractions?limit=25`, headers })
    expect(response.statusCode).toBe(200)
    expect(response.headers['cache-control']).toContain('no-store')
    expect(list).toHaveBeenCalledWith({ organizationId, documentId, limit: 25 })

    const tenantSelector = await app.inject({ method: 'GET', url: `/api/v1/documents/${documentId}/extractions?organizationId=${otherOrganizationId}`, headers })
    expect(tenantSelector.statusCode).toBe(400)
    expect(list).toHaveBeenCalledTimes(1)
    await app.close()
  })

  it('requires Idempotency-Key before persistence', async () => {
    process.env.FRONTEND_ORIGIN = trustedOrigin
    const { app, createOrReplay } = await build()
    const response = await app.inject({
      method: 'POST', url: `/api/v1/documents/${documentId}/extractions`,
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin },
      payload: { schemaVersion: 'invoice-v1' },
    })
    expect(response.statusCode).toBe(400)
    expect(createOrReplay).not.toHaveBeenCalled()
    await app.close()
  })

  it('rejects mutation without manage permission before persistence', async () => {
    process.env.FRONTEND_ORIGIN = trustedOrigin
    const { app, createOrReplay } = await build('viewer')
    const response = await app.inject({
      method: 'POST', url: `/api/v1/documents/${documentId}/extractions`,
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin, 'idempotency-key': 'extract-1' },
      payload: { schemaVersion: 'invoice-v1' },
    })
    expect(response.statusCode).toBe(403)
    expect(createOrReplay).not.toHaveBeenCalled()
    await app.close()
  })

  it('creates in session tenant with idempotency and records an audit event', async () => {
    process.env.FRONTEND_ORIGIN = trustedOrigin
    const { app, createOrReplay, record } = await build()
    const response = await app.inject({
      method: 'POST', url: `/api/v1/documents/${documentId}/extractions`,
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin, 'idempotency-key': 'extract-1' },
      payload: { schemaVersion: 'invoice-v1' },
    })
    expect(response.statusCode).toBe(202)
    expect(createOrReplay).toHaveBeenCalledWith({ organizationId, documentId, schemaVersion: 'invoice-v1', idempotencyKey: 'extract-1' })
    expect(record).toHaveBeenCalledWith(expect.objectContaining({ organizationId, actorUserId: userId, action: 'document.extraction_requested', resourceId: extractionId }))
    await app.close()
  })

  it('maps cross-tenant or missing document creation to stable 404 without audit', async () => {
    process.env.FRONTEND_ORIGIN = trustedOrigin
    const { app, createOrReplay, record } = await build()
    createOrReplay.mockRejectedValueOnce(new Error('DOCUMENT_NOT_FOUND'))
    const response = await app.inject({
      method: 'POST', url: `/api/v1/documents/${documentId}/extractions`,
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin, 'idempotency-key': 'extract-2' },
      payload: { schemaVersion: 'invoice-v1' },
    })
    expect(response.statusCode).toBe(404)
    expect(response.json().error.code).toBe('NOT_FOUND')
    expect(record).not.toHaveBeenCalled()
    await app.close()
  })
})
