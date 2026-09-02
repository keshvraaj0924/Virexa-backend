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
})
