import Fastify from 'fastify'
import cookie from '@fastify/cookie'
import { describe, expect, it, vi } from 'vitest'
import type { AuthRepository } from '../src/auth/repository.js'
import { AuthenticationRequiredError, PermissionDeniedError } from '../src/auth/context.js'
import { apiFailure } from '../src/contracts/http.js'
import { organizationRoutes } from '../src/organization/routes.js'
import type { PostgresOrganizationRepository } from '../src/organization/repository.js'

const organizationId = '11111111-1111-4111-8111-111111111111'
const userId = '22222222-2222-4222-8222-222222222222'

function authRepository(role: 'admin' | 'viewer' = 'admin'): AuthRepository {
  return {
    register: vi.fn(), login: vi.fn(), listActiveSessions: vi.fn(), revokeSession: vi.fn(),
    revokeOwnedSession: vi.fn(), revokeOtherSessions: vi.fn(), ping: vi.fn(),
    getSession: vi.fn(async (token: string) => token === 'valid-token' ? {
      user: { id: userId, email: 'admin@example.test', displayName: 'Admin', role, organizationId, organizationName: 'Acme' },
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    } : null),
  } as AuthRepository
}

async function build(role: 'admin' | 'viewer' = 'admin') {
  const app = Fastify()
  await app.register(cookie)
  const hierarchy = vi.fn(async () => ({ branches: [], departments: [], personas: [] }))
  const organizationRepository = { hierarchy } as unknown as PostgresOrganizationRepository
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
    if (error instanceof PermissionDeniedError) return reply.code(403).send(apiFailure('FORBIDDEN', error.message, request.id))
    throw error
  })
  await app.register(organizationRoutes, { authRepository: authRepository(role), organizationRepository })
  return { app, hierarchy }
}

describe('organization hierarchy HTTP boundary', () => {
  it('rejects unauthenticated requests without touching organization data', async () => {
    const { app, hierarchy } = await build()
    const response = await app.inject({ method: 'GET', url: '/api/v1/organization/hierarchy' })
    expect(response.statusCode).toBe(401)
    expect(hierarchy).not.toHaveBeenCalled()
    await app.close()
  })

  it('rejects authenticated users without organization:manage', async () => {
    const { app, hierarchy } = await build('viewer')
    const response = await app.inject({ method: 'GET', url: '/api/v1/organization/hierarchy', headers: { cookie: 'virexa_session=valid-token' } })
    expect(response.statusCode).toBe(403)
    expect(hierarchy).not.toHaveBeenCalled()
    await app.close()
  })

  it('derives tenant scope from the authenticated session and marks the response non-cacheable', async () => {
    const { app, hierarchy } = await build('admin')
    const response = await app.inject({ method: 'GET', url: '/api/v1/organization/hierarchy?limit=25&status=active', headers: { cookie: 'virexa_session=valid-token' } })
    expect(response.statusCode).toBe(200)
    expect(response.headers['cache-control']).toContain('no-store')
    expect(hierarchy).toHaveBeenCalledWith(organizationId, { limit: 25, status: 'active' })
    await app.close()
  })

  it('rejects client-controlled tenant selectors and invalid bounds', async () => {
    const { app, hierarchy } = await build('admin')
    const headers = { cookie: 'virexa_session=valid-token' }
    const tenantSelector = await app.inject({ method: 'GET', url: '/api/v1/organization/hierarchy?organizationId=33333333-3333-4333-8333-333333333333', headers })
    const excessiveLimit = await app.inject({ method: 'GET', url: '/api/v1/organization/hierarchy?limit=101', headers })
    expect(tenantSelector.statusCode).toBe(400)
    expect(excessiveLimit.statusCode).toBe(400)
    expect(hierarchy).not.toHaveBeenCalled()
    await app.close()
  })
})
