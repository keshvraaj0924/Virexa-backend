import type { AuthenticatedContext } from '../auth/context.js'
import type { Workflow, WorkflowStatus } from '../contracts/workflows.js'

/**
 * Workflow mutation policy combines tenant scope, RBAC, and resource ownership.
 * Broad workflow:manage is reserved for organization managers/admins; users
 * with workflow:create may update only workflows they created.
 */
export function canManageWorkflow(context: AuthenticatedContext, workflow: Workflow): boolean {
  if (workflow.organizationId !== context.user.organizationId) return false
  if (context.permissions.includes('workflow:manage')) return true
  return context.permissions.includes('workflow:create') && workflow.createdByUserId === context.user.id
}

const ALLOWED_STATUS_TRANSITIONS: Record<WorkflowStatus, readonly WorkflowStatus[]> = {
  draft: ['active'],
  active: ['paused', 'archived'],
  paused: ['active', 'archived'],
  archived: [],
}

export function canTransitionWorkflowStatus(current: WorkflowStatus, next: WorkflowStatus): boolean {
  return current === next || ALLOWED_STATUS_TRANSITIONS[current].includes(next)
}
