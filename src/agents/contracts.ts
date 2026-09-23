import { z } from 'zod'

export const agentStatusSchema = z.enum(['draft', 'active', 'paused', 'archived'])
export type AgentStatus = z.infer<typeof agentStatusSchema>

const optionalTenantResourceId = z.string().uuid().nullable().optional()

export const createAgentSchema = z.object({
  name: z.string().trim().min(2).max(120),
  description: z.string().trim().max(1000).nullable().optional(),
  personaId: optionalTenantResourceId,
  branchId: optionalTenantResourceId,
  departmentId: optionalTenantResourceId,
  instructions: z.string().trim().min(1).max(12000),
}).strict()

export const updateAgentSchema = z.object({
  name: z.string().trim().min(2).max(120).optional(),
  description: z.string().trim().max(1000).nullable().optional(),
  personaId: optionalTenantResourceId,
  branchId: optionalTenantResourceId,
  departmentId: optionalTenantResourceId,
  instructions: z.string().trim().min(1).max(12000).optional(),
  status: agentStatusSchema.optional(),
  expectedVersion: z.number().int().positive(),
}).strict().refine(
  (value) => Object.keys(value).some((key) => key !== 'expectedVersion'),
  { message: 'At least one mutable agent field is required.' },
)

export const listAgentsQuerySchema = z.object({
  status: agentStatusSchema.optional(),
  limit: z.coerce.number().int().min(1).max(100).default(24),
  cursor: z.string().trim().min(1).max(2048).optional(),
}).strict()

export const agentProjectionSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  description: z.string().nullable(),
  personaId: z.string().uuid().nullable(),
  branchId: z.string().uuid().nullable(),
  departmentId: z.string().uuid().nullable(),
  instructions: z.string(),
  status: agentStatusSchema,
  version: z.number().int().positive(),
  createdByUserId: z.string().uuid(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
}).strict()

export const listAgentsResponseSchema = z.object({
  items: z.array(agentProjectionSchema),
  nextCursor: z.string().nullable(),
}).strict()

export type CreateAgentInput = z.infer<typeof createAgentSchema>
export type UpdateAgentInput = z.infer<typeof updateAgentSchema>
export type ListAgentsQuery = z.infer<typeof listAgentsQuerySchema>
export type AgentProjection = z.infer<typeof agentProjectionSchema>
export type ListAgentsResponse = z.infer<typeof listAgentsResponseSchema>
