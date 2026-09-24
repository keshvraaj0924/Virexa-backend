import { createHash } from 'node:crypto'
import { Pool } from 'pg'
import type { AgentProjection, AgentStatus, CreateAgentInput, UpdateAgentInput } from './contracts.js'

export interface AgentListPage { items: AgentProjection[]; nextCursor: string | null }
export interface AgentRepository {
  create(organizationId: string, userId: string, input: CreateAgentInput): Promise<AgentProjection>
  createIdempotent(organizationId: string, userId: string, input: CreateAgentInput, idempotencyKey: string): Promise<{ agent: AgentProjection; replayed: boolean }>
  listPage(organizationId: string, input: { limit: number; status?: AgentStatus; cursor?: string }): Promise<AgentListPage>
  getById(organizationId: string, agentId: string): Promise<AgentProjection | null>
  update(organizationId: string, agentId: string, input: UpdateAgentInput): Promise<AgentProjection | null>
  close?(): Promise<void>
}

export class AgentIdempotencyKeyReuseError extends Error {
  constructor() { super('The idempotency key was already used with a different request.'); this.name = 'AGENT_IDEMPOTENCY_KEY_REUSED' }
}
export class InvalidAgentCursorError extends Error {
  constructor() { super('The agent cursor is invalid.'); this.name = 'INVALID_AGENT_CURSOR' }
}

type AgentCursor = { createdAt: string; id: string }
const columns = 'id, created_by_user_id, name, description, persona_id, branch_id, department_id, instructions, status, version, created_at, updated_at'

function mapAgent(row: any): AgentProjection {
  return {
    id: row.id, name: row.name, description: row.description ?? null,
    personaId: row.persona_id ?? null, branchId: row.branch_id ?? null, departmentId: row.department_id ?? null,
    instructions: row.instructions, status: row.status as AgentStatus, version: row.version,
    createdByUserId: row.created_by_user_id, createdAt: row.created_at.toISOString(), updatedAt: row.updated_at.toISOString(),
  }
}
function normalizedCreatePayload(input: CreateAgentInput): CreateAgentInput {
  return {
    name: input.name.trim(), description: input.description?.trim() || null,
    personaId: input.personaId ?? null, branchId: input.branchId ?? null, departmentId: input.departmentId ?? null,
    instructions: input.instructions.trim(),
  }
}
function requestHash(input: CreateAgentInput): string {
  return createHash('sha256').update(JSON.stringify(normalizedCreatePayload(input))).digest('hex')
}
function encodeCursor(agent: AgentProjection) {
  return Buffer.from(JSON.stringify({ createdAt: agent.createdAt, id: agent.id } satisfies AgentCursor), 'utf8').toString('base64url')
}
function decodeCursor(cursor: string): AgentCursor {
  try {
    const value = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8')) as Partial<AgentCursor>
    if (typeof value.createdAt !== 'string' || Number.isNaN(Date.parse(value.createdAt)) || typeof value.id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.id)) throw new InvalidAgentCursorError()
    return { createdAt: value.createdAt, id: value.id }
  } catch (error) { if (error instanceof InvalidAgentCursorError) throw error; throw new InvalidAgentCursorError() }
}

export class PostgresAgentRepository implements AgentRepository {
  constructor(private readonly pool: Pool) {}

  async create(organizationId: string, userId: string, input: CreateAgentInput) {
    const normalized = normalizedCreatePayload(input)
    const result = await this.pool.query(
      `INSERT INTO agents (organization_id, created_by_user_id, name, description, persona_id, branch_id, department_id, instructions)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING ${columns}`,
      [organizationId, userId, normalized.name, normalized.description, normalized.personaId, normalized.branchId, normalized.departmentId, normalized.instructions],
    )
    return mapAgent(result.rows[0])
  }

  async createIdempotent(organizationId: string, userId: string, input: CreateAgentInput, idempotencyKey: string) {
    const normalized = normalizedCreatePayload(input)
    const hash = requestHash(normalized)
    const client = await this.pool.connect()
    try {
      await client.query('BEGIN')
      const inserted = await client.query(
        `INSERT INTO agent_idempotency_keys (organization_id, idempotency_key, request_hash)
         VALUES ($1, $2, $3)
         ON CONFLICT (organization_id, idempotency_key) DO NOTHING
         RETURNING request_hash, agent_id`,
        [organizationId, idempotencyKey, hash],
      )
      let keyRecord = inserted.rows[0]
      const replayed = !keyRecord
      if (!keyRecord) {
        const existing = await client.query(
          `SELECT request_hash, agent_id FROM agent_idempotency_keys
           WHERE organization_id = $1 AND idempotency_key = $2 FOR UPDATE`,
          [organizationId, idempotencyKey],
        )
        keyRecord = existing.rows[0]
        if (!keyRecord) throw new Error('Agent idempotency reservation disappeared before it could be read')
      }
      if (keyRecord.request_hash !== hash) throw new AgentIdempotencyKeyReuseError()
      if (keyRecord.agent_id) {
        const existing = await client.query(`SELECT ${columns} FROM agents WHERE organization_id = $1 AND id = $2`, [organizationId, keyRecord.agent_id])
        if (!existing.rows[0]) throw new Error('Agent idempotency record references a missing agent')
        await client.query('COMMIT')
        return { agent: mapAgent(existing.rows[0]), replayed: true }
      }
      const agentResult = await client.query(
        `INSERT INTO agents (organization_id, created_by_user_id, name, description, persona_id, branch_id, department_id, instructions)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING ${columns}`,
        [organizationId, userId, normalized.name, normalized.description, normalized.personaId, normalized.branchId, normalized.departmentId, normalized.instructions],
      )
      const agent = mapAgent(agentResult.rows[0])
      await client.query(
        `UPDATE agent_idempotency_keys SET agent_id = $3 WHERE organization_id = $1 AND idempotency_key = $2`,
        [organizationId, idempotencyKey, agent.id],
      )
      await client.query('COMMIT')
      return { agent, replayed }
    } catch (error) {
      await client.query('ROLLBACK')
      throw error
    } finally { client.release() }
  }

  async listPage(organizationId: string, input: { limit: number; status?: AgentStatus; cursor?: string }) {
    const values: unknown[] = [organizationId]; const predicates = ['organization_id = $1']
    if (input.status !== undefined) { values.push(input.status); predicates.push(`status = $${values.length}`) }
    if (input.cursor !== undefined) { const cursor = decodeCursor(input.cursor); values.push(cursor.createdAt, cursor.id); predicates.push(`(created_at, id) < ($${values.length - 1}::timestamptz, $${values.length}::uuid)`) }
    values.push(input.limit + 1)
    const result = await this.pool.query(`SELECT ${columns} FROM agents WHERE ${predicates.join(' AND ')} ORDER BY created_at DESC, id DESC LIMIT $${values.length}`, values)
    const mapped = result.rows.map(mapAgent); const hasMore = mapped.length > input.limit; const items = hasMore ? mapped.slice(0, input.limit) : mapped
    return { items, nextCursor: hasMore && items.length ? encodeCursor(items[items.length - 1]) : null }
  }

  async getById(organizationId: string, agentId: string) {
    const result = await this.pool.query(`SELECT ${columns} FROM agents WHERE organization_id = $1 AND id = $2`, [organizationId, agentId])
    return result.rows[0] ? mapAgent(result.rows[0]) : null
  }

  async update(organizationId: string, agentId: string, input: UpdateAgentInput) {
    const fields: string[] = []; const values: unknown[] = [organizationId, agentId, input.expectedVersion]
    const add = (column: string, value: unknown) => { values.push(value); fields.push(`${column} = $${values.length}`) }
    if (input.name !== undefined) add('name', input.name.trim())
    if (input.description !== undefined) add('description', input.description?.trim() || null)
    if (input.personaId !== undefined) add('persona_id', input.personaId)
    if (input.branchId !== undefined) add('branch_id', input.branchId)
    if (input.departmentId !== undefined) add('department_id', input.departmentId)
    if (input.instructions !== undefined) add('instructions', input.instructions.trim())
    if (input.status !== undefined) add('status', input.status)
    fields.push('version = version + 1', 'updated_at = now()')
    const result = await this.pool.query(`UPDATE agents SET ${fields.join(', ')} WHERE organization_id = $1 AND id = $2 AND version = $3 RETURNING ${columns}`, values)
    return result.rows[0] ? mapAgent(result.rows[0]) : null
  }

  async close() { await this.pool.end() }
}
