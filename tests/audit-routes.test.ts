import Fastify from 'fastify'
import cookie from '@fastify/cookie'
import { describe, expect, it, vi } from 'vitest'
import type { AuthRepository } from '../src/auth/repository.js'
import { AuthenticationRequiredError, PermissionDeniedError } from '../src/auth/context.js'
import { apiFailure } from '../src/contracts/http.js'
import { auditRoutes } from '../src/audit/routes.js'
import type { AuditService } from '../src/audit/service.js'

const organizationId = '11111111-1111-4111-8111-111111111111'
const userId = '22222222-2222-4222-8222-222222222222'

function authRepository(role: 'manager' | 'viewer' = 'manager'): AuthRepository {
  return {
    register: vi.fn(), login: vi.fn(), listActiveSessions: vi.fn(), revokeSession: vi.fn(),
    revokeOwnedSession: vi.fn(), revokeOtherSessions: vi.fn(), ping: vi.fn(),
    getSession: vi.fn(async (token: string) => token === 'valid-token' ? {
      user: { id: userId, email: 'manager@example.test', displayName: 'Manager', role, organizationId, organizationName: 'Acme' },
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    } : null),
  } as AuthRepository
}

async function build(role: 'manager' | 'viewer' = 'manager') {
  const app = Fastify()
  await app.register(cookie)
  const listForOrganization = vi.fn(async () => [])
  const auditService = { listForOrganization } as unknown as AuditService
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
    if (error instanceof PermissionDeniedError) return reply.code(403).send(apiFailure('FORBIDDEN', error.message, request.id))
    throw error
  })
  await app.register(auditRoutes, { authRepository: authRepository(role), auditService })
  return { app, listForOrganization }
}

describe('audit read HTTP boundary', () => {
  it('rejects unauthenticated requests without reading audit data', async () => {
    const { app, listForOrganization } = await build()
    const response = await app.inject({ method: 'GET', url: '/api/v1/audit/events' })
    expect(response.statusCode).toBe(401)
    expect(listForOrganization).not.toHaveBeenCalled()
    await app.close()
  })

  it('rejects authenticated users without audit:read', async () => {
    const { app, listForOrganization } = await build('viewer')
    const response = await app.inject({ method: 'GET', url: '/api/v1/audit/events', headers: { cookie: 'virexa_session=valid-token' } })
    expect(response.statusCode).toBe(403)
    expect(listForOrganization).not.toHaveBeenCalled()
    await app.close()
  })

  it('derives organization scope from session and marks audit data non-cacheable', async () => {
    const { app, listForOrganization } = await build()
    const response = await app.inject({ method: 'GET', url: '/api/v1/audit/events?limit=25', headers: { cookie: 'virexa_session=valid-token' } })
    expect(response.statusCode).toBe(200)
    expect(response.headers['cache-control']).toContain('no-store')
    expect(listForOrganization).toHaveBeenCalledWith(organizationId, 25)
    await app.close()
  })

  it('rejects client-controlled tenant selectors and invalid limits', async () => {
    const { app, listForOrganization } = await build()
    const headers = { cookie: 'virexa_session=valid-token' }
    const tenantSelector = await app.inject({ method: 'GET', url: '/api/v1/audit/events?organizationId=33333333-3333-4333-8333-333333333333', headers })
    const excessiveLimit = await app.inject({ method: 'GET', url: '/api/v1/audit/events?limit=101', headers })
    expect(tenantSelector.statusCode).toBe(400)
    expect(excessiveLimit.statusCode).toBe(400)
    expect(listForOrganization).not.toHaveBeenCalled()
    await app.close()
  })
})
