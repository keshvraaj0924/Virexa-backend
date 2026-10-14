import { describe, expect, it, vi } from 'vitest'
import { PostgresAgentRepository } from './repository.js'

const organizationId = '11111111-1111-4111-8111-111111111111'
const actorA = '22222222-2222-4222-8222-222222222222'
const actorB = '33333333-3333-4333-8333-333333333333'
const agentId = '44444444-4444-4444-8444-444444444444'
const idempotencyKey = 'unique-create-operation-001'
const createInput = { name: 'Invoice verifier', instructions: 'Validate invoices.' }

const agentRow = {
  id: agentId,
  created_by_user_id: actorA,
  name: createInput.name,
  description: null,
  persona_id: null,
  branch_id: null,
  department_id: null,
  instructions: createInput.instructions,
  status: 'draft',
  version: 1,
  created_at: new Date('2026-10-14T00:00:00.000Z'),
  updated_at: new Date('2026-10-14T00:00:00.000Z'),
}

function transactionalPool(replay = false, missingAgent = false) {
  let requestHash = ''
  const query = vi.fn(async (sql: string, params: unknown[] = []) => {
    if (sql === 'BEGIN' || sql === 'COMMIT' || sql === 'ROLLBACK') return { rows: [] }
    if (sql.includes('INSERT INTO agent_idempotency_keys')) {
      requestHash = String(params[3])
      return { rows: replay ? [] : [{ request_hash: requestHash, agent_id: null }] }
    }
    if (sql.includes('SELECT request_hash, agent_id')) {
      return { rows: [{ request_hash: requestHash, agent_id: agentId }] }
    }
    if (sql.includes('SELECT id, created_by_user_id')) {
      return { rows: missingAgent ? [] : [{ ...agentRow, created_by_user_id: params[2] }] }
    }
    if (sql.includes('INSERT INTO agents')) {
      return { rows: [{ ...agentRow, created_by_user_id: params[1] }] }
    }
    if (sql.includes('UPDATE agent_idempotency_keys')) return { rows: [] }
    throw new Error(`Unexpected SQL: ${sql}`)
  })
  const client = { query, release: vi.fn() }
  const pool = { connect: vi.fn(async () => client) }
  return { pool, query, client }
}

describe('Agent creation idempotency: authenticated actor isolation', () => {
  it('reserves the same key independently for different actors within one tenant', async () => {
    const { pool, query } = transactionalPool()
    const repository = new PostgresAgentRepository(pool as never)

    const first = await repository.createIdempotent(organizationId, actorA, createInput, idempotencyKey)
    const second = await repository.createIdempotent(organizationId, actorB, createInput, idempotencyKey)

    expect(first.replayed).toBe(false)
    expect(second.replayed).toBe(false)
    const reservations = query.mock.calls.filter(([sql]) => sql.includes('INSERT INTO agent_idempotency_keys'))
    expect(reservations).toHaveLength(2)
    expect(reservations[0][0]).toContain('ON CONFLICT (organization_id, created_by_user_id, idempotency_key)')
    expect(reservations[0][1]?.slice(0, 3)).toEqual([organizationId, actorA, idempotencyKey])
    expect(reservations[1][1]?.slice(0, 3)).toEqual([organizationId, actorB, idempotencyKey])
    const updates = query.mock.calls.filter(([sql]) => sql.includes('UPDATE agent_idempotency_keys'))
    expect(updates.map(([, params]) => params?.slice(0, 3))).toEqual([
      [organizationId, actorA, idempotencyKey],
      [organizationId, actorB, idempotencyKey],
    ])
  })

  it('replays only the requesting actor\'s Agent', async () => {
    const { pool, query } = transactionalPool(true)
    const replay = await new PostgresAgentRepository(pool as never).createIdempotent(
      organizationId, actorA, createInput, idempotencyKey,
    )
    expect(replay.replayed).toBe(true)
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('created_by_user_id = $2 AND idempotency_key = $3 FOR UPDATE'),
      [organizationId, actorA, idempotencyKey],
    )
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('AND created_by_user_id = $3'),
      [organizationId, agentId, actorA],
    )
    expect(query.mock.calls.filter(([sql]) => sql.includes('INSERT INTO agents'))).toHaveLength(0)
  })

  it('fails closed and rolls back when a replay Agent is not owned by the actor', async () => {
    const { pool, query, client } = transactionalPool(true, true)
    await expect(
      new PostgresAgentRepository(pool as never).createIdempotent(
        organizationId, actorB, createInput, idempotencyKey,
      ),
    ).rejects.toThrow('references a missing agent')
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('AND created_by_user_id = $3'),
      [organizationId, agentId, actorB],
    )
    expect(query).toHaveBeenCalledWith('ROLLBACK')
    expect(query).not.toHaveBeenCalledWith('COMMIT')
    expect(client.release).toHaveBeenCalledOnce()
  })
})
