import { randomUUID } from 'node:crypto'
import Fastify from 'fastify'
import cors from '@fastify/cors'
import cookie from '@fastify/cookie'
import helmet from '@fastify/helmet'
import rateLimit from '@fastify/rate-limit'
import { Pool } from 'pg'
import { z } from 'zod'
import { apiFailure, apiSuccess } from './contracts/http.js'
import type { AuthSession, LoginRequest, RegisterRequest } from './contracts/auth.js'
import type { CreateWorkflowRequest, UpdateWorkflowRequest } from './contracts/workflows.js'
import { createAuthRepository, type AuthRepository } from './auth/repository.js'
import { assertTrustedOrigin } from './auth/origin-guard.js'
import { requireAuthenticated, requireAnyPermission, requirePermission, AuthenticationRequiredError, PermissionDeniedError } from './auth/context.js'
import { AuditService } from './audit/service.js'
import { IdempotencyKeyReuseError, PostgresWorkflowRepository } from './workflows/repository.js'
import { canManageWorkflow, canTransitionWorkflowStatus } from './workflows/policy.js'
import { assertProductionConfiguration, frontendOrigin } from './config/runtime.js'
import { markSensitiveResponse } from './http/cache-policy.js'

assertProductionConfiguration()

export const app = Fastify({ logger: true, requestIdHeader: 'x-request-id', genReqId: () => randomUUID() })
await app.register(helmet, { contentSecurityPolicy: false })
await app.register(cookie)
await app.register(cors, { origin: frontendOrigin(), credentials: true })
await app.register(rateLimit, {
  global: false,
  max: 100,
  timeWindow: '1 minute',
  errorResponseBuilder: (request) => apiFailure('RATE_LIMITED', 'Too many requests. Please try again later.', request.id),
})

const requestStartTimes = new WeakMap<object, bigint>()
app.addHook('onRequest', async (request) => {
  requestStartTimes.set(request, process.hrtime.bigint())
})
app.addHook('onResponse', async (request, reply) => {
  const startedAt = requestStartTimes.get(request)
  if (startedAt === undefined) return
  const durationMs = Number(process.hrtime.bigint() - startedAt) / 1_000_000
  request.log.info({
    requestId: request.id,
    method: request.method,
    route: request.routeOptions.url,
    statusCode: reply.statusCode,
    durationMs: Number(durationMs.toFixed(3)),
  }, 'Request completed')
})

const registerSchema = z.object({ displayName: z.string().trim().min(2).max(120), email: z.string().trim().email().max(320), password: z.string().min(12).max(128), organizationName: z.string().trim().min(2).max(160) }).strict()
const loginSchema = z.object({ email: z.string().trim().email().max(320), password: z.string().min(1).max(128) }).strict()
const auditQuerySchema = z.object({ limit: z.coerce.number().int().min(1).max(100).default(50) }).strict()
const workflowQuerySchema = z.object({ limit: z.coerce.number().int().min(1).max(100).default(50) }).strict()
const workflowIdSchema = z.string().uuid()
const idempotencyKeySchema = z.string().trim().min(16).max(255)
const createWorkflowSchema = z.object({ name: z.string().trim().min(1).max(160), description: z.string().trim().max(4000).nullable().optional() }).strict()
const updateWorkflowSchema = z.object({ name: z.string().trim().min(1).max(160).optional(), description: z.string().trim().max(4000).nullable().optional(), status: z.enum(['draft', 'active', 'paused', 'archived']).optional() }).strict().refine((value) => Object.keys(value).length > 0, { message: 'At least one workflow field must be provided.' })

let repository: AuthRepository | undefined
let auditService: AuditService | undefined
let workflowRepository: PostgresWorkflowRepository | undefined
function databaseUrl(): string {
  const value = process.env.DATABASE_URL
  if (!value) { const error = new Error('DATABASE_URL is not configured'); error.name = 'DEPENDENCY_UNAVAILABLE'; throw error }
  return value
}
function authRepository(): AuthRepository { repository ??= createAuthRepository(databaseUrl()); return repository }
function audits(): AuditService { auditService ??= new AuditService(new Pool({ connectionString: databaseUrl(), max: 5 })); return auditService }
function workflows(): PostgresWorkflowRepository { workflowRepository ??= new PostgresWorkflowRepository(new Pool({ connectionString: databaseUrl(), max: 10 })); return workflowRepository }
function sessionCookieOptions() { return { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'lax' as const, path: '/', maxAge: 60 * 60 * 8 } }

app.addHook('onClose', async () => {
  await Promise.all([
    repository?.close?.(),
    auditService?.close(),
    workflowRepository?.close(),
  ])
})

app.setErrorHandler((error, request, reply) => {
  const errorName = error instanceof Error ? error.name : undefined
  if (errorName === 'UNTRUSTED_ORIGIN') return reply.code(403).send(apiFailure('UNTRUSTED_ORIGIN', 'Request origin is not trusted.', request.id))
  if (errorName === 'DEPENDENCY_UNAVAILABLE') return reply.code(503).send(apiFailure('DEPENDENCY_UNAVAILABLE', 'A required service is unavailable.', request.id))
  if (error instanceof AuthenticationRequiredError) return reply.code(401).send(apiFailure('UNAUTHENTICATED', error.message, request.id))
  if (error instanceof PermissionDeniedError) return reply.code(403).send(apiFailure('FORBIDDEN', error.message, request.id))
  if (error instanceof IdempotencyKeyReuseError) return reply.code(409).send(apiFailure('IDEMPOTENCY_KEY_REUSED', error.message, request.id))
  request.log.error({ err: error }, 'Unhandled request error')
  return reply.code(500).send(apiFailure('INTERNAL_ERROR', 'An unexpected server error occurred.', request.id))
})

app.get('/health', async (request) => apiSuccess({ status: 'ok' }, request.id))

app.get('/ready', async (request, reply) => {
  try {
    await authRepository().ping()
    return reply.send(apiSuccess({ status: 'ready', dependencies: { database: 'ok' } }, request.id))
  } catch (error) {
    request.log.warn({ err: error }, 'Readiness dependency check failed')
    return reply.code(503).send(apiFailure('DEPENDENCY_UNAVAILABLE', 'A required service is unavailable.', request.id))
  }
})

app.post<{ Body: RegisterRequest }>('/api/v1/auth/register', {
  config: { rateLimit: { max: 10, timeWindow: '5 minutes' } },
}, async (request, reply) => {
  assertTrustedOrigin(request)
  const parsed = registerSchema.safeParse(request.body)
  if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Registration data is invalid.', request.id, parsed.error.flatten().fieldErrors))
  try {
    const result = await authRepository().register(parsed.data)
    const session: AuthSession = { user: result.user, expiresAt: result.expiresAt }
    reply.setCookie('virexa_session', result.sessionToken, sessionCookieOptions())
    await audits().record({ organizationId: result.user.organizationId, actorUserId: result.user.id, action: 'identity.registered', resourceType: 'user', resourceId: result.user.id, requestId: request.id, metadata: { role: result.user.role } })
    return reply.code(201).send(apiSuccess(session, request.id))
  } catch (error: any) {
    if (error?.code === '23505') return reply.code(409).send(apiFailure('EMAIL_ALREADY_REGISTERED', 'An account already exists for this organization and email.', request.id))
    throw error
  }
})

app.post<{ Body: LoginRequest }>('/api/v1/auth/login', {
  config: { rateLimit: { max: 10, timeWindow: '5 minutes' } },
}, async (request, reply) => {
  assertTrustedOrigin(request)
  const parsed = loginSchema.safeParse(request.body)
  if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Email and password are required.', request.id))
  const result = await authRepository().login(parsed.data.email, parsed.data.password)
  if (!result) return reply.code(401).send(apiFailure('INVALID_CREDENTIALS', 'Email or password is incorrect.', request.id))
  const session: AuthSession = { user: result.user, expiresAt: result.expiresAt }
  reply.setCookie('virexa_session', result.sessionToken, sessionCookieOptions())
  await audits().record({ organizationId: result.user.organizationId, actorUserId: result.user.id, action: 'identity.login_succeeded', resourceType: 'session', requestId: request.id })
  return reply.send(apiSuccess(session, request.id))
})

app.get('/api/v1/auth/session', async (request, reply) => {
  markSensitiveResponse(reply)
  const token = request.cookies.virexa_session
  if (!token) return reply.code(401).send(apiFailure('UNAUTHENTICATED', 'Authentication is required.', request.id))
  const session = await authRepository().getSession(token)
  if (!session) { reply.clearCookie('virexa_session', { path: '/' }); return reply.code(401).send(apiFailure('UNAUTHENTICATED', 'Authentication is required.', request.id)) }
  return reply.send(apiSuccess(session, request.id))
})

app.post('/api/v1/auth/logout', async (request, reply) => {
  assertTrustedOrigin(request)
  const token = request.cookies.virexa_session
  if (token) {
    const session = await authRepository().getSession(token)
    await authRepository().revokeSession(token)
    if (session) await audits().record({ organizationId: session.user.organizationId, actorUserId: session.user.id, action: 'identity.logout', resourceType: 'session', requestId: request.id })
  }
  reply.clearCookie('virexa_session', { path: '/' })
  return reply.send(apiSuccess({ success: true }, request.id))
})

app.get('/api/v1/me', async (request, reply) => {
  markSensitiveResponse(reply)
  const context = await requireAuthenticated(request, authRepository())
  requirePermission(context, 'platform:read')
  return reply.send(apiSuccess(context, request.id))
})

app.get('/api/v1/audit/events', async (request, reply) => {
  markSensitiveResponse(reply)
  const context = await requireAuthenticated(request, authRepository())
  requirePermission(context, 'audit:read')
  const parsed = auditQuerySchema.safeParse(request.query ?? {})
  if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Audit query parameters are invalid.', request.id))
  const events = await audits().listForOrganization(context.user.organizationId, parsed.data.limit)
  return reply.send(apiSuccess(events, request.id))
})

app.get('/api/v1/workflows', async (request, reply) => {
  markSensitiveResponse(reply)
  const context = await requireAuthenticated(request, authRepository())
  requirePermission(context, 'workflow:read')
  const parsed = workflowQuerySchema.safeParse(request.query ?? {})
  if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Workflow query parameters are invalid.', request.id))
  return reply.send(apiSuccess(await workflows().list(context.user.organizationId, parsed.data.limit), request.id))
})

app.post<{ Body: CreateWorkflowRequest }>('/api/v1/workflows', async (request, reply) => {
  assertTrustedOrigin(request)
  const context = await requireAuthenticated(request, authRepository())
  requirePermission(context, 'workflow:create')
  const parsed = createWorkflowSchema.safeParse(request.body)
  if (!parsed.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Workflow data is invalid.', request.id, parsed.error.flatten().fieldErrors))
  const rawKey = request.headers['idempotency-key']
  const parsedKey = idempotencyKeySchema.safeParse(typeof rawKey === 'string' ? rawKey : '')
  if (!parsedKey.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'A valid Idempotency-Key header is required.', request.id, { idempotencyKey: ['Use a 16-255 character unique key.'] }))
  const result = await workflows().createIdempotent(context.user.organizationId, context.user.id, parsed.data, parsedKey.data)
  if (!result.replayed) {
    await audits().record({ organizationId: context.user.organizationId, actorUserId: context.user.id, action: 'workflow.created', resourceType: 'workflow', resourceId: result.workflow.id, requestId: request.id })
  }
  return reply.code(result.replayed ? 200 : 201).send(apiSuccess(result.workflow, request.id))
})

app.get<{ Params: { workflowId: string } }>('/api/v1/workflows/:workflowId', async (request, reply) => {
  markSensitiveResponse(reply)
  const context = await requireAuthenticated(request, authRepository())
  requirePermission(context, 'workflow:read')
  const parsedId = workflowIdSchema.safeParse(request.params.workflowId)
  if (!parsedId.success) return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Workflow ID is invalid.', request.id))
  const workflow = await workflows().getById(context.user.organizationId, parsedId.data)
  if (!workflow) return reply.code(404).send(apiFailure('NOT_FOUND', 'Workflow was not found.', request.id))
  return reply.send(apiSuccess(workflow, request.id))
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
      return reply.code(409).send(apiFailure('WORKFLOW_CONFLICT', `Workflow changed before this update could be applied; current status is ${current.status}.`, request.id))
    }
    return reply.code(404).send(apiFailure('NOT_FOUND', 'Workflow was not found.', request.id))
  }
  await audits().record({ organizationId: context.user.organizationId, actorUserId: context.user.id, action: 'workflow.updated', resourceType: 'workflow', resourceId: workflow.id, requestId: request.id, metadata: { status: workflow.status } })
  return reply.send(apiSuccess(workflow, request.id))
})
