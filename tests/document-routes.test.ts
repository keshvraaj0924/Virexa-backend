import Fastify from 'fastify'
import cookie from '@fastify/cookie'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AuthRepository } from '../src/auth/repository.js'
import { AuthenticationRequiredError, PermissionDeniedError } from '../src/auth/context.js'
import type { AuditService } from '../src/audit/service.js'
import { apiFailure } from '../src/contracts/http.js'
import type { DocumentRecord } from '../src/contracts/documents.js'
import { DuplicateDocumentError, type DocumentRepository } from '../src/documents/repository.js'
import { registerDocumentRoutes } from '../src/documents/routes.js'

const organizationId = '11111111-1111-4111-8111-111111111111'
const userId = '22222222-2222-4222-8222-222222222222'
const documentId = '33333333-3333-4333-8333-333333333333'
const otherOrganizationId = '44444444-4444-4444-8444-444444444444'
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
  receivedAt: '2026-09-19T12:00:00.000Z',
  updatedAt: '2026-09-19T12:00:00.000Z',
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
  const create = vi.fn(async () => document)
  const list = vi.fn(async () => ({ items: [document], nextCursor: null }))
  const getById = vi.fn(async (_organizationId: string, id: string) => id === documentId ? document : null)
  const record = vi.fn(async () => undefined)
  const documentRepository = { create, list, getById } as DocumentRepository
  const auditService = { record } as unknown as AuditService
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
    if (error instanceof PermissionDeniedError) return reply.code(403).send(apiFailure('FORBIDDEN', error.message, request.id))
    if (error.name === 'UNTRUSTED_ORIGIN') return reply.code(403).send(apiFailure('UNTRUSTED_ORIGIN', error.message, request.id))
    throw error
  })
  await registerDocumentRoutes(app, { authRepository: authRepository(role), auditService, documentRepository })
  return { app, create, list, getById, record }
}

afterEach(() => { delete process.env.FRONTEND_ORIGIN })

describe('document HTTP boundary', () => {
  it('requires authentication before document repository access', async () => {
    const { app, list } = await build()
    const response = await app.inject({ method: 'GET', url: '/api/v1/documents' })
    expect(response.statusCode).toBe(401)
    expect(list).not.toHaveBeenCalled()
    await app.close()
  })

  it('derives tenant scope from the authenticated session and rejects tenant selectors', async () => {
    const { app, list } = await build()
    const headers = { cookie: 'virexa_session=valid-token' }
    const response = await app.inject({ method: 'GET', url: '/api/v1/documents?limit=25', headers })
    expect(response.statusCode).toBe(200)
    expect(response.headers['cache-control']).toContain('no-store')
    expect(list).toHaveBeenCalledWith(organizationId, { limit: 25 })

    const tenantSelector = await app.inject({ method: 'GET', url: `/api/v1/documents?organizationId=${otherOrganizationId}`, headers })
    expect(tenantSelector.statusCode).toBe(400)
    expect(list).toHaveBeenCalledTimes(1)
    await app.close()
  })

  it('scopes document lookup to the authenticated organization', async () => {
    const { app, getById } = await build()
    const response = await app.inject({ method: 'GET', url: `/api/v1/documents/${documentId}`, headers: { cookie: 'virexa_session=valid-token' } })
    expect(response.statusCode).toBe(200)
    expect(getById).toHaveBeenCalledWith(organizationId, documentId)
    await app.close()
  })

  it('rejects document creation without create permission before persistence', async () => {
    process.env.FRONTEND_ORIGIN = trustedOrigin
    const { app, create } = await build('viewer')
    const response = await app.inject({
      method: 'POST', url: '/api/v1/documents',
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin },
      payload: { originalFileName: 'invoice.pdf', mediaType: 'application/pdf', sizeBytes: 1024, checksumSha256: 'a'.repeat(64) },
    })
    expect(response.statusCode).toBe(403)
    expect(create).not.toHaveBeenCalled()
    await app.close()
  })

  it('creates in session tenant and records an audit event', async () => {
    process.env.FRONTEND_ORIGIN = trustedOrigin
    const { app, create, record } = await build()
    const payload = { originalFileName: 'invoice.pdf', mediaType: 'application/pdf', sizeBytes: 1024, checksumSha256: 'a'.repeat(64) }
    const response = await app.inject({
      method: 'POST', url: '/api/v1/documents',
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin }, payload,
    })
    expect(response.statusCode).toBe(201)
    expect(create).toHaveBeenCalledWith(organizationId, { ...payload, source: 'upload' })
    expect(record).toHaveBeenCalledWith(expect.objectContaining({ organizationId, actorUserId: userId, action: 'document.received', resourceId: documentId }))
    await app.close()
  })

  it.each([
    ['checksum', 'DOCUMENT_CHECKSUM_CONFLICT'],
    ['external_reference', 'DOCUMENT_EXTERNAL_REFERENCE_CONFLICT'],
  ] as const)('maps %s duplicate intake to a stable 409 contract', async (reason, code) => {
    process.env.FRONTEND_ORIGIN = trustedOrigin
    const { app, create, record } = await build()
    create.mockRejectedValueOnce(new DuplicateDocumentError(reason))
    const response = await app.inject({
      method: 'POST', url: '/api/v1/documents',
      headers: { cookie: 'virexa_session=valid-token', origin: trustedOrigin },
      payload: { originalFileName: 'invoice.pdf', mediaType: 'application/pdf', sizeBytes: 1024, checksumSha256: 'a'.repeat(64) },
    })
    expect(response.statusCode).toBe(409)
    expect(response.json().error.code).toBe(code)
    expect(record).not.toHaveBeenCalled()
    await app.close()
  })
})
