export const WORKFLOW_STATUSES = ['draft', 'active', 'paused', 'archived'] as const
export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number]

export interface Workflow {
  id: string
  organizationId: string
  createdByUserId: string
  name: string
  description: string | null
  status: WorkflowStatus
  version: number
  createdAt: string
  updatedAt: string
}

export interface CreateWorkflowRequest {
  name: string
  description?: string | null
}

export interface UpdateWorkflowRequest {
  expectedVersion: number
  name?: string
  description?: string | null
  status?: WorkflowStatus
}
