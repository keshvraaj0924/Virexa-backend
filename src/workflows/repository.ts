import { createHash } from 'node:crypto'
import { Pool } from 'pg'
import type { CreateWorkflowRequest, UpdateWorkflowRequest, Workflow, WorkflowStatus } from '../contracts/workflows.js'

export interface WorkflowRepository {
  create(organizationId: string, userId: string, input: CreateWorkflowRequest): Promise<Workflow>
  createIdempotent(organizationId: string, userId: string, input: CreateWorkflowRequest, idempotencyKey: string): Promise<{ workflow: Workflow; replayed: boolean }>
  list(organizationId: string, limit: number): Promise<Workflow[]>
  getById(organizationId: string, workflowId: string): Promise<Workflow | null>
  update(organizationId: string, workflowId: string, input: UpdateWorkflowRequest, expectedStatus?: WorkflowStatus): Promise<Workflow | null>
  close?(): Promise<void>
}

export class IdempotencyKeyReuseError extends Error {
  constructor() {
    super('The idempotency key was already used with a different request.')
    this.name = 'IDEMPOTENCY_KEY_REUSED'
  }
}

function mapWorkflow(row: any): Workflow {
  return { id: row.id, organizationId: row.organization_id, createdByUserId: row.created_by_user_id, name: row.name, description: row.description ?? null, status: row.status as WorkflowStatus, createdAt: row.created_at.toISOString(), updatedAt: row.updated_at.toISOString() }
}

const columns = 'id, organization_id, created_by_user_id, name, description, status, created_at, updated_at'

function normalizedCreatePayload(input: CreateWorkflowRequest): CreateWorkflowRequest {
  return { name: input.name.trim(), description: input.description?.trim() || null }
}

function requestHash(input: CreateWorkflowRequest): string {
  return createHash('sha256').update(JSON.stringify(normalizedCreatePayload(input))).digest('hex')
}

export class PostgresWorkflowRepository implements WorkflowRepository {
  constructor(private readonly pool: Pool) {}

  async create(organizationId: string, userId: string, input: CreateWorkflowRequest) {
    const normalized = normalizedCreatePayload(input)
    const result = await this.pool.query(`INSERT INTO workflows (organization_id, created_by_user_id, name, description) VALUES ($1, $2, $3, $4) RETURNING ${columns}`, [organizationId, userId, normalized.name, normalized.description])
    return mapWorkflow(result.rows[0])
  }

  async createIdempotent(organizationId: string, userId: string, input: CreateWorkflowRequest, idempotencyKey: string) {
    const normalized = normalizedCreatePayload(input)
    const hash = requestHash(normalized)
    const client = await this.pool.connect()
    try {
      await client.query('BEGIN')
      // Serialize only callers sharing the same tenant/key. The lock is transaction-scoped.
      await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0))", [organizationId, idempotencyKey])
      await client.query(
        `INSERT INTO workflow_idempotency_keys (organization_id, idempotency_key, request_hash)
         VALUES ($1, $2, $3)
         ON CONFLICT (organization_id, idempotency_key) DO NOTHING`,
        [organizationId, idempotencyKey, hash],
      )
      const keyResult = await client.query(
        `SELECT request_hash, workflow_id FROM workflow_idempotency_keys
         WHERE organization_id = $1 AND idempotency_key = $2 FOR UPDATE`,
        [organizationId, idempotencyKey],
      )
      const keyRecord = keyResult.rows[0]
      if (keyRecord.request_hash !== hash) throw new IdempotencyKeyReuseError()

      if (keyRecord.workflow_id) {
        const existing = await client.query(`SELECT ${columns} FROM workflows WHERE organization_id = $1 AND id = $2`, [organizationId, keyRecord.workflow_id])
        if (!existing.rows[0]) throw new Error('Idempotency record references a missing workflow')
        await client.query('COMMIT')
        return { workflow: mapWorkflow(existing.rows[0]), replayed: true }
      }

      const workflowResult = await client.query(
        `INSERT INTO workflows (organization_id, created_by_user_id, name, description)
         VALUES ($1, $2, $3, $4) RETURNING ${columns}`,
        [organizationId, userId, normalized.name, normalized.description],
      )
      const workflow = mapWorkflow(workflowResult.rows[0])
      await client.query(
        `UPDATE workflow_idempotency_keys SET workflow_id = $3
         WHERE organization_id = $1 AND idempotency_key = $2`,
        [organizationId, idempotencyKey, workflow.id],
      )
      await client.query('COMMIT')
      return { workflow, replayed: false }
    } catch (error) {
      await client.query('ROLLBACK')
      throw error
    } finally {
      client.release()
    }
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

  async close() {
    await this.pool.end()
  }
}
