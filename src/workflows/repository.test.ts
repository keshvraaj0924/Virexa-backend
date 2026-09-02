import { describe, expect, it, vi } from 'vitest'
import { PostgresWorkflowRepository } from './repository.js'

function poolWithRows(rows: unknown[]) {
  return { query: vi.fn().mockResolvedValue({ rows }) } as any
}

describe('PostgresWorkflowRepository', () => {
  it('scopes list queries to the authenticated organization', async () => {
    const pool = poolWithRows([])
    await new PostgresWorkflowRepository(pool).list('org-a', 25)
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('WHERE organization_id = $1'), ['org-a', 25])
  })

  it('scopes get-by-id queries to the authenticated organization', async () => {
    const pool = poolWithRows([])
    const workflow = await new PostgresWorkflowRepository(pool).getById('org-a', 'workflow-a')
    expect(workflow).toBeNull()
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('WHERE organization_id = $1 AND id = $2'), ['org-a', 'workflow-a'])
  })

  it('scopes updates to the authenticated organization and writes only supplied fields', async () => {
    const row = { id: 'workflow-a', organization_id: 'org-a', created_by_user_id: 'user-a', name: 'Updated', description: null, status: 'active', created_at: new Date(), updated_at: new Date() }
    const pool = poolWithRows([row])
    const workflow = await new PostgresWorkflowRepository(pool).update('org-a', 'workflow-a', { name: 'Updated', status: 'active' })
    expect(workflow?.name).toBe('Updated')
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('WHERE organization_id = $1 AND id = $2'), ['org-a', 'workflow-a', 'Updated', 'active'])
  })

  it('uses the current status as an optimistic concurrency guard for lifecycle updates', async () => {
    const row = { id: 'workflow-a', organization_id: 'org-a', created_by_user_id: 'user-a', name: 'Updated', description: null, status: 'active', created_at: new Date(), updated_at: new Date() }
    const pool = poolWithRows([row])
    await new PostgresWorkflowRepository(pool).update('org-a', 'workflow-a', { status: 'active' }, 'draft')
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('AND status = $5'), ['org-a', 'workflow-a', 'active', 'draft'])
  })
})
