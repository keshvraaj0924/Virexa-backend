import { describe, expect, it } from 'vitest'
import {
  createWorkflowRequestSchema,
  updateWorkflowRequestSchema,
  workflowListQuerySchema,
  workflowListResponseSchema,
} from './workflows.js'

describe('workflow v1 contracts', () => {
  it('keeps organization and creator identity server-authoritative on create', () => {
    expect(createWorkflowRequestSchema.safeParse({ name: 'Invoice review', organizationId: crypto.randomUUID() }).success).toBe(false)
    expect(createWorkflowRequestSchema.safeParse({ name: 'Invoice review', createdByUserId: crypto.randomUUID() }).success).toBe(false)
  })

  it('requires an optimistic concurrency token and a real update', () => {
    expect(updateWorkflowRequestSchema.safeParse({ status: 'active' }).success).toBe(false)
    expect(updateWorkflowRequestSchema.safeParse({ expectedVersion: 1 }).success).toBe(false)
    expect(updateWorkflowRequestSchema.safeParse({ expectedVersion: 1, status: 'active' }).success).toBe(true)
  })

  it('rejects client-authoritative version and tenant fields', () => {
    expect(updateWorkflowRequestSchema.safeParse({ expectedVersion: 1, name: 'A', version: 2 }).success).toBe(false)
    expect(updateWorkflowRequestSchema.safeParse({ expectedVersion: 1, name: 'A', organizationId: crypto.randomUUID() }).success).toBe(false)
  })

  it('bounds list pagination and status filters', () => {
    expect(workflowListQuerySchema.parse({})).toEqual({ limit: 25 })
    expect(workflowListQuerySchema.safeParse({ limit: 101 }).success).toBe(false)
    expect(workflowListQuerySchema.safeParse({ status: 'deleted' }).success).toBe(false)
    expect(workflowListQuerySchema.safeParse({ cursor: '' }).success).toBe(false)
  })

  it('requires the paginated response envelope', () => {
    const now = new Date().toISOString()
    const workflow = {
      id: crypto.randomUUID(),
      organizationId: crypto.randomUUID(),
      createdByUserId: crypto.randomUUID(),
      name: 'Invoice review',
      description: null,
      status: 'draft',
      version: 1,
      createdAt: now,
      updatedAt: now,
    }
    expect(workflowListResponseSchema.safeParse({ items: [workflow], nextCursor: null }).success).toBe(true)
    expect(workflowListResponseSchema.safeParse([workflow]).success).toBe(false)
  })
})
