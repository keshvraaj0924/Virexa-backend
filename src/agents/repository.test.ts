import { describe, expect, it, vi } from 'vitest'
import { AgentIdempotencyKeyReuseError, InvalidAgentCursorError, PostgresAgentRepository } from './repository.js'

function poolWithRows(rows: unknown[]) { return { query: vi.fn().mockResolvedValue({ rows }) } as any }
const agentRow = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', created_by_user_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  name: 'Ops Agent', description: null, persona_id: null, branch_id: null, department_id: null,
  instructions: 'Handle AP.', status: 'draft', version: 1, created_at: new Date(), updated_at: new Date(),
}
const createInput = { name: ' Ops Agent ', description: null, personaId: null, branchId: null, departmentId: null, instructions: ' Handle AP. ' }

describe('PostgresAgentRepository', () => {
  it('derives tenant scope from the server argument on every list query', async () => {
    const pool = poolWithRows([])
    await new PostgresAgentRepository(pool).listPage('org-a', { limit: 24, status: 'active' })
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('WHERE organization_id = $1 AND status = $2'), ['org-a', 'active', 25])
  })

  it('scopes get-by-id to organization and does not enumerate another tenant', async () => {
    const pool = poolWithRows([])
    expect(await new PostgresAgentRepository(pool).getById('org-a', 'agent-a')).toBeNull()
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('WHERE organization_id = $1 AND id = $2'), ['org-a', 'agent-a'])
  })

  it('uses compare-and-swap versioning for mutations', async () => {
    const row = { ...agentRow, status: 'active', version: 4 }
    const pool = poolWithRows([row])
    const agent = await new PostgresAgentRepository(pool).update('org-a', row.id, { expectedVersion: 3, status: 'active' })
    expect(agent?.version).toBe(4)
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('WHERE organization_id = $1 AND id = $2 AND version = $3'), ['org-a', row.id, 3, 'active'])
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('version = version + 1'), expect.any(Array))
  })

  it('rejects malformed opaque cursors before querying storage', async () => {
    const pool = poolWithRows([])
    await expect(new PostgresAgentRepository(pool).listPage('org-a', { limit: 24, cursor: 'not-a-cursor' })).rejects.toBeInstanceOf(InvalidAgentCursorError)
    expect(pool.query).not.toHaveBeenCalled()
  })

  it('creates the agent and idempotency record atomically using normalized payload semantics', async () => {
    const client = { query: vi.fn(), release: vi.fn() }
    client.query
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [{ request_hash: 'placeholder', agent_id: null }] })
    const pool = { connect: vi.fn().mockResolvedValue(client) } as any
    const repository = new PostgresAgentRepository(pool)

    // Capture the deterministic request hash, then return it from the reservation query.
    client.query.mockReset()
    client.query
      .mockResolvedValueOnce({ rows: [] })
      .mockImplementationOnce(async (_sql: string, values: unknown[]) => ({ rows: [{ request_hash: values[2], agent_id: null }] }))
      .mockResolvedValueOnce({ rows: [agentRow] })
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] })

    const result = await repository.createIdempotent('org-a', agentRow.created_by_user_id, createInput, 'agent-create-key-0001')
    expect(result).toMatchObject({ replayed: false, agent: { id: agentRow.id, name: 'Ops Agent' } })
    expect(client.query).toHaveBeenCalledWith(expect.stringContaining('INSERT INTO agents'), ['org-a', agentRow.created_by_user_id, 'Ops Agent', null, null, null, null, 'Handle AP.'])
    expect(client.query).toHaveBeenCalledWith(expect.stringContaining('UPDATE agent_idempotency_keys SET agent_id'), ['org-a', 'agent-create-key-0001', agentRow.id])
    expect(client.query).toHaveBeenLastCalledWith('COMMIT')
    expect(client.release).toHaveBeenCalledOnce()
  })

  it('replays the original tenant-scoped agent without creating a duplicate', async () => {
    const client = { query: vi.fn(), release: vi.fn() }
    client.query
      .mockResolvedValueOnce({ rows: [] })
      .mockResolvedValueOnce({ rows: [] })
      .mockImplementationOnce(async (_sql: string, values: unknown[]) => ({ rows: [{ request_hash: values[2] ?? 'unused', agent_id: agentRow.id }] }))
    const pool = { connect: vi.fn().mockResolvedValue(client) } as any

    // The request hash is not available in the SELECT values; derive it by observing the reservation call.
    let hash = ''
    client.query.mockReset()
    client.query.mockImplementation(async (sql: string, values?: unknown[]) => {
      if (sql === 'BEGIN' || sql === 'COMMIT') return { rows: [] }
      if (sql.includes('INSERT INTO agent_idempotency_keys')) { hash = String(values?.[2]); return { rows: [] } }
      if (sql.includes('SELECT request_hash, agent_id')) return { rows: [{ request_hash: hash, agent_id: agentRow.id }] }
      if (sql.includes('SELECT id, created_by_user_id')) return { rows: [agentRow] }
      throw new Error(`Unexpected query: ${sql}`)
    })

    const result = await new PostgresAgentRepository(pool).createIdempotent('org-a', agentRow.created_by_user_id, createInput, 'agent-create-key-0001')
    expect(result.replayed).toBe(true)
    expect(client.query).toHaveBeenCalledWith(expect.stringContaining('FROM agents WHERE organization_id = $1 AND id = $2'), ['org-a', agentRow.id])
    expect(client.query.mock.calls.filter(([sql]) => String(sql).includes('INSERT INTO agents'))).toHaveLength(0)
  })

  it('rejects reuse of an idempotency key for a different payload and rolls back', async () => {
    const client = { query: vi.fn(), release: vi.fn() }
    client.query.mockImplementation(async (sql: string) => {
      if (sql === 'BEGIN' || sql === 'ROLLBACK') return { rows: [] }
      if (sql.includes('INSERT INTO agent_idempotency_keys')) return { rows: [] }
      if (sql.includes('SELECT request_hash, agent_id')) return { rows: [{ request_hash: '0'.repeat(64), agent_id: agentRow.id }] }
      throw new Error(`Unexpected query: ${sql}`)
    })
    const pool = { connect: vi.fn().mockResolvedValue(client) } as any
    await expect(new PostgresAgentRepository(pool).createIdempotent('org-a', agentRow.created_by_user_id, createInput, 'agent-create-key-0001')).rejects.toBeInstanceOf(AgentIdempotencyKeyReuseError)
    expect(client.query).toHaveBeenCalledWith('ROLLBACK')
    expect(client.release).toHaveBeenCalledOnce()
  })
})
