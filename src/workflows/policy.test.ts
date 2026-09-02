import { describe, expect, it } from 'vitest'
import { canManageWorkflow } from './policy.js'
import type { AuthenticatedContext } from '../auth/context.js'
import type { Workflow } from '../contracts/workflows.js'

const workflow: Workflow = {
  id: 'workflow-1', organizationId: 'org-1', createdByUserId: 'user-1', name: 'Invoice approval', description: null,
  status: 'draft', createdAt: '2026-09-02T00:00:00.000Z', updatedAt: '2026-09-02T00:00:00.000Z',
}

function context(userId: string, permissions: AuthenticatedContext['permissions']): AuthenticatedContext {
  return { user: { id: userId, organizationId: 'org-1', organizationName: 'Acme', displayName: 'Test User', email: 'test@example.com', role: 'operator' }, expiresAt: '2026-09-03T00:00:00.000Z', permissions }
}

describe('workflow mutation policy', () => {
  it('allows managers with workflow:manage to update any workflow in their tenant', () => {
    expect(canManageWorkflow(context('manager-1', ['workflow:manage']), workflow)).toBe(true)
  })

  it('allows creators with workflow:create to update only their own workflow', () => {
    expect(canManageWorkflow(context('user-1', ['workflow:create']), workflow)).toBe(true)
    expect(canManageWorkflow(context('user-2', ['workflow:create']), workflow)).toBe(false)
  })

  it('denies users without mutation permission even when they created the workflow', () => {
    expect(canManageWorkflow(context('user-1', ['workflow:read']), workflow)).toBe(false)
  })
})
