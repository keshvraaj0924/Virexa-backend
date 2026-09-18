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

  it('scopes updates by tenant and expected version while incrementing the version atomically', async () => {
    const row = { id: 'workflow-a', organization_id: 'org-a', created_by_user_id: 'user-a', name: 'Updated', description: null, status: 'active', version: 4, created_at: new Date(), updated_at: new Date() }
    const pool = poolWithRows([row])
    const workflow = await new PostgresWorkflowRepository(pool).update('org-a', 'workflow-a', { expectedVersion: 3, name: 'Updated', status: 'active' })
    expect(workflow?.name).toBe('Updated')
    expect(workflow?.version).toBe(4)
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('WHERE organization_id = $1 AND id = $2 AND version = $3'), ['org-a', 'workflow-a', 3, 'Updated', 'active'])
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('version = version + 1'), expect.any(Array))
  })

  it('combines version and current-status guards for lifecycle updates', async () => {
    const row = { id: 'workflow-a', organization_id: 'org-a', created_by_user_id: 'user-a', name: 'Updated', description: null, status: 'active', version: 8, created_at: new Date(), updated_at: new Date() }
    const pool = poolWithRows([row])
    await new PostgresWorkflowRepository(pool).update('org-a', 'workflow-a', { expectedVersion: 7, status: 'active' }, 'draft')
    expect(pool.query).toHaveBeenCalledWith(expect.stringContaining('AND version = $3 AND status = $5'), ['org-a', 'workflow-a', 7, 'active', 'draft'])
  })
})
