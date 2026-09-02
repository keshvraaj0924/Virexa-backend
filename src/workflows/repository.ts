import { Pool } from 'pg'
import type { CreateWorkflowRequest, Workflow, WorkflowStatus } from '../contracts/workflows.js'

export interface WorkflowRepository {
  create(organizationId: string, userId: string, input: CreateWorkflowRequest): Promise<Workflow>
  list(organizationId: string, limit: number): Promise<Workflow[]>
  getById(organizationId: string, workflowId: string): Promise<Workflow | null>
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
}
