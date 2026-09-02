import { randomUUID } from 'node:crypto'
import Fastify from 'fastify'
import cookie from '@fastify/cookie'
import cors from '@fastify/cors'
import helmet from '@fastify/helmet'
import type { CreateWorkflowRequest, UpdateWorkflowRequest } from './contracts/workflows.js'
import { createWorkflowSchema, updateWorkflowSchema, workflowIdSchema, workflowQuerySchema } from './workflows/validation.js'
import { authRepository, registerUser, loginUser, logoutUser, requireAuthenticated, requireAnyPermission, requirePermission, AuthenticationRequiredError, PermissionDeniedError } from './auth/index.js'
import { assertTrustedOrigin } from './auth/origin-guard.js'
import { apiFailure, apiSuccess } from './contracts/api.js'
import { AuditService } from './audit/service.js'
import { PostgresWorkflowRepository } from './workflows/repository.js'
import { canManageWorkflow, canTransitionWorkflowStatus } from './workflows/policy.js'

const app = Fastify({ logger: true, requestIdHeader: 'x-request-id', genReqId: () => randomUUID() })
await app.register(helmet, { contentSecurityPolicy: false })
await app.register(cookie, { hook: 'onRequest' })
await app.register(cors, { origin: process.env.FRONTEND_ORIGIN ?? 'http://localhost:3000', credentials: true })

const pool = authRepository().pool
const workflows = () => new PostgresWorkflowRepository(pool)
const audits = () => new AuditService(pool)

app.setErrorHandler((error, request, reply) => {
  request.log.error({ err: error, requestId: request.id }, 'Unhandled API error')
  return reply.code(500).send(apiFailure('INTERNAL_ERROR', 'An unexpected error occurred.', request.id))
})

app.get('/health/live', async (_request, reply) => reply.send({ data: { status: 'ok' } }))

app.get('/api/v1/me', async (request, reply) => {
  try {
    return reply.send(apiSuccess(await requireAuthenticated(request, authRepository()), request.id))
  } catch (error) {
    if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
    throw error
  }
})

app.patch<{ Params: { workflowId: string }; Body: UpdateWorkflowRequest }>('/api/v1/workflows/:workflowId', async (request, reply) => {
  assertTrustedOrigin(request)
  const context = await requireAuthenticated(request, authRepository())
  requireAnyPermission(context, ['workflow:create', 'workflow:manage'])
  const parsedId = workflowIdSchema.safeParse(request.params.workflowId)
  if (!parsedId.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Workflow ID is invalid.', request.id))
  const parsed = updateWorkflowSchema.safeParse(request.body)
  if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Workflow update data is invalid.', request.id, parsed.error.flatten().fieldErrors))
  const existing = await workflows().getById(context.user.organizationId, parsedId.data)
  if (!existing) return reply.code(404).send(apiFailure('NOT_FOUND', 'Workflow was not found.', request.id))
  if (!canManageWorkflow(context, existing)) return reply.code(403).send(apiFailure('FORBIDDEN', 'You cannot modify this workflow.', request.id))
  if (parsed.data.status && !canTransitionWorkflowStatus(existing.status, parsed.data.status)) {
    return reply.code(409).send(apiFailure('INVALID_STATE_TRANSITION', `Workflow cannot transition from ${existing.status} to ${parsed.data.status}.`, request.id))
  }
  const workflow = await workflows().update(context.user.organizationId, existing.id, parsed.data, parsed.data.status ? existing.status : undefined)
  if (!workflow) {
    const current = await workflows().getById(context.user.organizationId, existing.id)
    if (current && parsed.data.status && current.status !== existing.status) {
      return reply.code(409).send(apiFailure('INVALID_STATE_TRANSITION', `Workflow changed before this update could be applied; current status is ${current.status}.`, request.id))
    }
    return reply.code(404).send(apiFailure('NOT_FOUND', 'Workflow was not found.', request.id))
  }
  await audits().record({ organizationId: context.user.organizationId, actorUserId: context.user.id, action: 'workflow.updated', resourceType: 'workflow', resourceId: workflow.id, requestId: request.id, metadata: { status: workflow.status } })
  return reply.send(apiSuccess(workflow, request.id))
})

const port = Number(process.env.PORT ?? 4000)
await app.listen({ port, host: process.env.HOST ?? '0.0.0.0' })
