import type { FastifyPluginAsync } from 'fastify'
import { z } from 'zod'
import type { AuthRepository } from '../auth/repository.js'
import { requireAuthenticated, requirePermission } from '../auth/context.js'
import { apiFailure, apiSuccess } from '../contracts/http.js'
import { markSensitiveResponse } from '../http/cache-policy.js'
import type { AuditService } from './service.js'

const auditQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(50),
}).strict()

export interface AuditRoutesOptions {
  authRepository: AuthRepository
  auditService: AuditService
}

/**
 * Read-only audit API. Organization scope is derived exclusively from the
 * authenticated session; callers cannot select a tenant or organization.
 */
export const auditRoutes: FastifyPluginAsync<AuditRoutesOptions> = async (app, options) => {
  app.get('/api/v1/audit/events', async (request, reply) => {
    markSensitiveResponse(reply)

    const context = await requireAuthenticated(request, options.authRepository)
    requirePermission(context, 'audit:read')

    const parsed = auditQuerySchema.safeParse(request.query ?? {})
    if (!parsed.success) {
      return reply.code(400).send(apiFailure(
        'VALIDATION_ERROR',
        'Audit event query parameters are invalid.',
        request.id,
        parsed.error.flatten().fieldErrors,
      ))
    }

    const events = await options.auditService.listForOrganization(
      context.user.organizationId,
      parsed.data.limit,
    )

    return reply.send(apiSuccess({ events }, request.id))
  })
}
