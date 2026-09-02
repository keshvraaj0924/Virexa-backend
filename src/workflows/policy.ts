import type { AuthenticatedContext } from '../auth/context.js'
import type { Workflow } from '../contracts/workflows.js'

/**
 * Workflow mutation policy combines RBAC with resource ownership.
 * Broad workflow:manage is reserved for organization managers/admins; users
 * with workflow:create may update only workflows they created.
 */
export function canManageWorkflow(context: AuthenticatedContext, workflow: Workflow): boolean {
  if (context.permissions.includes('workflow:manage')) return true
  return context.permissions.includes('workflow:create') && workflow.createdByUserId === context.user.id
}
