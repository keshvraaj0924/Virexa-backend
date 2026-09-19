import type { FastifyPluginAsync } from 'fastify'
import { z } from 'zod'
import type { AuthRepository } from '../auth/repository.js'
import { requireAuthenticated, requirePermission } from '../auth/context.js'
import { apiFailure, apiSuccess } from '../contracts/http.js'
import { markSensitiveResponse } from '../http/cache-policy.js'
import type { PostgresOrganizationRepository } from './repository.js'

const hierarchyQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(50),
  status: z.enum(['active', 'inactive']).optional(),
}).strict()

export interface OrganizationRoutesOptions {
  authRepository: AuthRepository
  organizationRepository: PostgresOrganizationRepository
}

/**
 * Read-only organization administration routes.
 *
 * Tenant scope is always derived from the authenticated session. The HTTP
 * contract deliberately accepts no organization/tenant identifier, preventing
 * callers from selecting another tenant even when they know its identifier.
 */
export const organizationRoutes: FastifyPluginAsync<OrganizationRoutesOptions> = async (app, options) => {
  app.get('/api/v1/organization/hierarchy', async (request, reply) => {
    markSensitiveResponse(reply)

    const context = await requireAuthenticated(request, options.authRepository)
    requirePermission(context, 'organization:manage')

    const parsed = hierarchyQuerySchema.safeParse(request.query ?? {})
    if (!parsed.success) {
      return reply.code(400).send(apiFailure(
        'VALIDATION_ERROR',
        'Organization hierarchy query parameters are invalid.',
        request.id,
        parsed.error.flatten().fieldErrors,
      ))
    }

    const hierarchy = await options.organizationRepository.hierarchy(
      context.user.organizationId,
      parsed.data,
    )

    return reply.send(apiSuccess(hierarchy, request.id))
  })
}
