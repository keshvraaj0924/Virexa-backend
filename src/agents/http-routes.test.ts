import Fastify from 'fastify'
import cookie from '@fastify/cookie'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthenticationRequiredError, PermissionDeniedError } from '../auth/context.js'
import { apiFailure } from '../contracts/http.js'
import { agentRoutes } from './http-routes.js'

const user = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  email: 'operator@example.com',
  displayName: 'Operator',
  role: 'operator' as const,
  organizationId: 'org-tenant-a',
  organizationName: 'Tenant A',
}
const agent = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  name: 'AP Agent',
  description: null,
  personaId: null,
  branchId: null,
  departmentId: null,
  instructions: 'Review invoices',
  status: 'draft' as const,
  version: 1,
  createdByUserId: user.id,
  createdAt: '2026-10-01T00:00:00.000Z',
  updatedAt: '2026-10-01T00:00:00.000Z',
}

function createTestApp() {
  const authRepository = { getSession: vi.fn(async (token: string) => token === 'valid-session' ? { user, expiresAt: '2026-10-11T12:00:00.000Z' } : null) }
  const agentRepository = {
    listPage: vi.fn(async (organizationId: string) => ({ items: organizationId === user.organizationId ? [agent] : [], nextCursor: null })),
    createIdempotent: vi.fn(async () => ({ agent, replayed: false })),
    getById: vi.fn(async (organizationId: string, id: string) => organizationId === user.organizationId && id === agent.id ? agent : null),
    update: vi.fn(async () => agent),
  }
  const auditService = { record: vi.fn(async () => undefined) }
  const app = Fastify()
  app.register(cookie)
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
    if (error instanceof PermissionDeniedError) return reply.code(403).send(apiFailure('FORBIDDEN', error.message, request.id))
    return reply.code(500).send(apiFailure('INTERNAL_ERROR', 'Unexpected test error', request.id))
  })
  app.register(agentRoutes, {
    authRepository: authRepository as never,
    agentRepository: agentRepository as never,
    auditService: auditService as never,
  })
  return { app, authRepository, agentRepository, auditService }
}

describe('Agent HTTP routes', () => {
  beforeEach(() => vi.clearAllMocks())

  it('rejects unauthenticated list requests before touching tenant data', async () => {
    const { app, agentRepository } = createTestApp()
    const response = await app.inject({ method: 'GET', url: '/api/v1/agents' })
    expect(response.statusCode).toBe(401)
    expect(response.json().error.code).toBe('UNAUTHENTICATED')
    expect(agentRepository.listPage).not.toHaveBeenCalled()
    await app.close()
  })

  it('derives list scope from the authenticated session, never from query input', async () => {
    const { app, agentRepository } = createTestApp()
    const response = await app.inject({
      method: 'GET',
      url: '/api/v1/agents?organizationId=org-tenant-b&limit=24',
      headers: { cookie: 'virexa_session=valid-session' },
    })
    expect(response.statusCode).toBe(200)
    expect(agentRepository.listPage).toHaveBeenCalledWith(user.organizationId, expect.objectContaining({ limit: 24 }))
    expect(response.json().data.items).toHaveLength(1)
    await app.close()
  })

  it('rejects malformed pagination input with the shared validation envelope', async () => {
    const { app, agentRepository } = createTestApp()
    const response = await app.inject({
      method: 'GET',
      url: '/api/v1/agents?limit=999',
      headers: { cookie: 'virexa_session=valid-session' },
    })
    expect(response.statusCode).toBe(400)
    expect(response.json().error.code).toBe('VALIDATION_ERROR')
    expect(agentRepository.listPage).not.toHaveBeenCalled()
    await app.close()
  })
})
