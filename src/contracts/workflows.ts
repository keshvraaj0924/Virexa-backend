import { z } from 'zod'

export const WORKFLOW_STATUSES = ['draft', 'active', 'paused', 'archived'] as const
export const workflowStatusSchema = z.enum(WORKFLOW_STATUSES)

const workflowNameSchema = z.string().trim().min(1).max(120)
const workflowDescriptionSchema = z.string().trim().max(1000).nullable()

export const workflowSchema = z.object({
  id: z.string().uuid(),
  organizationId: z.string().uuid(),
  createdByUserId: z.string().uuid(),
  name: workflowNameSchema,
  description: workflowDescriptionSchema,
  status: workflowStatusSchema,
  version: z.number().int().positive(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
})

// POST /api/v1/workflows
// Organization and creator scope are derived from the authenticated session.
export const createWorkflowRequestSchema = z.object({
  name: workflowNameSchema,
  description: workflowDescriptionSchema.optional(),
}).strict()

// PATCH /api/v1/workflows/:workflowId
// expectedVersion is the optimistic-concurrency token. Tenant scope, creator and
// version advancement remain server-authoritative.
export const updateWorkflowRequestSchema = z.object({
  expectedVersion: z.number().int().positive(),
  name: workflowNameSchema.optional(),
  description: workflowDescriptionSchema.optional(),
  status: workflowStatusSchema.optional(),
}).strict().refine(
  (value) => value.name !== undefined || value.description !== undefined || value.status !== undefined,
  { message: 'At least one workflow field must be updated' },
)

// GET /api/v1/workflows
// Opaque cursor pagination keeps persistence ordering details outside the public contract.
export const workflowListQuerySchema = z.object({
  status: workflowStatusSchema.optional(),
  limit: z.coerce.number().int().min(1).max(100).default(25),
  cursor: z.string().trim().min(1).max(1024).optional(),
}).strict()

export const workflowListResponseSchema = z.object({
  items: z.array(workflowSchema),
  nextCursor: z.string().nullable(),
})

export type WorkflowStatus = z.infer<typeof workflowStatusSchema>
export type Workflow = z.infer<typeof workflowSchema>
export type CreateWorkflowRequest = z.infer<typeof createWorkflowRequestSchema>
export type UpdateWorkflowRequest = z.infer<typeof updateWorkflowRequestSchema>
export type WorkflowListQuery = z.infer<typeof workflowListQuerySchema>
export type WorkflowListResponse = z.infer<typeof workflowListResponseSchema>
