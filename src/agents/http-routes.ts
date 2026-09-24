import type { FastifyPluginAsync } from 'fastify'
import { z } from 'zod'
import { apiFailure, apiSuccess } from '../contracts/http.js'
import { requireAnyPermission, requireAuthenticated, requirePermission } from '../auth/context.js'
import type { AuthRepository } from '../auth/repository.js'
import type { AuditService } from '../audit/service.js'
import { assertTrustedOrigin } from '../auth/origin-guard.js'
import { markSensitiveResponse } from '../http/cache-policy.js'
import { createAgentSchema, listAgentsQuerySchema, updateAgentSchema, type CreateAgentInput, type UpdateAgentInput } from './contracts.js'
import { AgentIdempotencyKeyReuseError, InvalidAgentCursorError, type AgentRepository } from './repository.js'
import { canManageAgent, canTransitionAgentStatus } from './policy.js'

const agentIdSchema = z.string().uuid()
const idempotencyKeySchema = z.string().trim().min(16).max(255)

export interface AgentRoutesOptions {
  authRepository: AuthRepository
  agentRepository: AgentRepository
  auditService: AuditService
}

function isHierarchyConstraintError(error: unknown): boolean {
  const code = (error as { code?: unknown })?.code
  return code === '23503'
}

export const agentRoutes: FastifyPluginAsync<AgentRoutesOptions> = async (app, options) => {
  const { authRepository, agentRepository, auditService } = options

  app.get('/api/v1/agents', async (request, reply) => {
    markSensitiveResponse(reply)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'agent:read')
    const parsed = listAgentsQuerySchema.safeParse(request.query ?? {})
    if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Agent query parameters are invalid.', request.id, parsed.error.flatten().fieldErrors))
    try {
      return reply.send(apiSuccess(await agentRepository.listPage(context.user.organizationId, parsed.data), request.id))
    } catch (error) {
      if (error instanceof InvalidAgentCursorError) return reply.code(400).send(apiFailure('INVALID_CURSOR', 'Agent cursor is invalid.', request.id))
      throw error
    }
  })

  app.post<{ Body: CreateAgentInput }>('/api/v1/agents', async (request, reply) => {
    assertTrustedOrigin(request)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'agent:create')
    const parsed = createAgentSchema.safeParse(request.body)
    if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Agent data is invalid.', request.id, parsed.error.flatten().fieldErrors))
    const rawKey = request.headers['idempotency-key']
    const parsedKey = idempotencyKeySchema.safeParse(typeof rawKey === 'string' ? rawKey : '')
    if (!parsedKey.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'A valid Idempotency-Key header is required.', request.id, { idempotencyKey: ['Use a 16-255 character unique key.'] }))
    try {
      const result = await agentRepository.createIdempotent(context.user.organizationId, context.user.id, parsed.data, parsedKey.data)
      if (!result.replayed) await auditService.record({ organizationId: context.user.organizationId, actorUserId: context.user.id, action: 'agent.created', resourceType: 'agent', resourceId: result.agent.id, requestId: request.id, metadata: { status: result.agent.status, version: result.agent.version } })
      return reply.code(result.replayed ? 200 : 201).send(apiSuccess(result.agent, request.id))
    } catch (error) {
      if (error instanceof AgentIdempotencyKeyReuseError) return reply.code(409).send(apiFailure('IDEMPOTENCY_KEY_REUSED', error.message, request.id))
      if (isHierarchyConstraintError(error)) return reply.code(422).send(apiFailure('INVALID_TENANT_REFERENCE', 'One or more Agent hierarchy references are invalid for this organization.', request.id))
      throw error
    }
  })

  app.get<{ Params: { agentId: string } }>('/api/v1/agents/:agentId', async (request, reply) => {
    markSensitiveResponse(reply)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'agent:read')
    const parsedId = agentIdSchema.safeParse(request.params.agentId)
    if (!parsedId.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Agent ID is invalid.', request.id))
    const agent = await agentRepository.getById(context.user.organizationId, parsedId.data)
    if (!agent) return reply.code(404).send(apiFailure('NOT_FOUND', 'Agent was not found.', request.id))
    return reply.send(apiSuccess(agent, request.id))
  })

  app.patch<{ Params: { agentId: string }; Body: UpdateAgentInput }>('/api/v1/agents/:agentId', async (request, reply) => {
    assertTrustedOrigin(request)
    const context = await requireAuthenticated(request, authRepository)
    requireAnyPermission(context, ['agent:create', 'agent:manage'])
    const parsedId = agentIdSchema.safeParse(request.params.agentId)
    if (!parsedId.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Agent ID is invalid.', request.id))
    const parsed = updateAgentSchema.safeParse(request.body)
    if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Agent update data is invalid.', request.id, parsed.error.flatten().fieldErrors))
    const existing = await agentRepository.getById(context.user.organizationId, parsedId.data)
    if (!existing) return reply.code(404).send(apiFailure('NOT_FOUND', 'Agent was not found.', request.id))
    if (!canManageAgent(context, existing)) return reply.code(403).send(apiFailure('FORBIDDEN', 'You cannot modify this agent.', request.id))
    if (parsed.data.expectedVersion !== existing.version) return reply.code(409).send(apiFailure('AGENT_CONFLICT', 'Agent changed before this update could be applied.', request.id))
    if (parsed.data.status && !canTransitionAgentStatus(existing.status, parsed.data.status)) return reply.code(409).send(apiFailure('INVALID_STATE_TRANSITION', `Agent cannot transition from ${existing.status} to ${parsed.data.status}.`, request.id))
    try {
      const agent = await agentRepository.update(context.user.organizationId, existing.id, parsed.data)
      if (!agent) return reply.code(409).send(apiFailure('AGENT_CONFLICT', 'Agent changed before this update could be applied.', request.id))
      await auditService.record({ organizationId: context.user.organizationId, actorUserId: context.user.id, action: 'agent.updated', resourceType: 'agent', resourceId: agent.id, requestId: request.id, metadata: { status: agent.status, version: agent.version } })
      return reply.send(apiSuccess(agent, request.id))
    } catch (error) {
      if (isHierarchyConstraintError(error)) return reply.code(422).send(apiFailure('INVALID_TENANT_REFERENCE', 'One or more Agent hierarchy references are invalid for this organization.', request.id))
      throw error
    }
  })
}
