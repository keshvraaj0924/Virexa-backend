import { describe, expect, it, vi } from 'vitest'
import { InvalidAgentCursorError, PostgresAgentRepository } from './repository.js'

function poolWithRows(rows: unknown[]) { return { query: vi.fn().mockResolvedValue({ rows }) } as any }

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
    const row = { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', created_by_user_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', name: 'Ops Agent', description: null, persona_id: null, branch_id: null, department_id: null, instructions: 'Handle AP.', status: 'active', version: 4, created_at: new Date(), updated_at: new Date() }
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
})
