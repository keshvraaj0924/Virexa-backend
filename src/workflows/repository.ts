import { Pool } from 'pg'
import type { CreateWorkflowRequest, UpdateWorkflowRequest, Workflow, WorkflowStatus } from '../contracts/workflows.js'

export interface WorkflowRepository {
  create(organizationId: string, userId: string, input: CreateWorkflowRequest): Promise<Workflow>
  list(organizationId: string, limit: number): Promise<Workflow[]>
  getById(organizationId: string, workflowId: string): Promise<Workflow | null>
  update(organizationId: string, workflowId: string, input: UpdateWorkflowRequest, expectedStatus?: WorkflowStatus): Promise<Workflow | null>
}

function mapWorkflow(row: any): Workflow {
  return { id: row.id, organizationId: row.organization_id, createdByUserId: row.created_by_user_id, name: row.name, description: row.description ?? null, status: row.status as WorkflowStatus, createdAt: row.created_at.toISOString(), updatedAt: row.updated_at.toISOString() }
}

const columns = 'id, organization_id, created_by_user_id, name, description, status, created_at, updated_at'

export class PostgresWorkflowRepository implements WorkflowRepository {
  constructor(private readonly pool: Pool) {}
  async create(organizationId: string, userId: string, input: CreateWorkflowRequest) {
    const result = await this.pool.query(`INSERT INTO workflows (organization_id, created_by_user_id, name, description) VALUES ($1, $2, $3, $4) RETURNING ${columns}`, [organizationId, userId, input.name.trim(), input.description?.trim() || null])
    return mapWorkflow(result.rows[0])
  }
  async list(organizationId: string, limit: number) {
    const result = await this.pool.query(`SELECT ${columns} FROM workflows WHERE organization_id = $1 ORDER BY created_at DESC LIMIT $2`, [organizationId, limit])
    return result.rows.map(mapWorkflow)
  }
  async getById(organizationId: string, workflowId: string) {
    const result = await this.pool.query(`SELECT ${columns} FROM workflows WHERE organization_id = $1 AND id = $2`, [organizationId, workflowId])
    return result.rows[0] ? mapWorkflow(result.rows[0]) : null
  }
  async update(organizationId: string, workflowId: string, input: UpdateWorkflowRequest, expectedStatus?: WorkflowStatus) {
    const fields: string[] = []
    const values: unknown[] = [organizationId, workflowId]
    if (input.name !== undefined) { values.push(input.name.trim()); fields.push(`name = $${values.length}`) }
    if (input.description !== undefined) { values.push(input.description?.trim() || null); fields.push(`description = $${values.length}`) }
    if (input.status !== undefined) { values.push(input.status); fields.push(`status = $${values.length}`) }
    if (fields.length === 0) return this.getById(organizationId, workflowId)
    fields.push('updated_at = now()')
    const statusPredicate = expectedStatus === undefined ? '' : ` AND status = $${values.push(expectedStatus)}`
    const result = await this.pool.query(`UPDATE workflows SET ${fields.join(', ')} WHERE organization_id = $1 AND id = $2${statusPredicate} RETURNING ${columns}`, values)
    return result.rows[0] ? mapWorkflow(result.rows[0]) : null
  }
}
