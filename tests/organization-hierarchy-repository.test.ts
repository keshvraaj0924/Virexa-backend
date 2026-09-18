import assert from 'node:assert/strict'
import test from 'node:test'
import type { Pool } from 'pg'
import { PostgresOrganizationRepository } from '../src/organization/repository.js'

interface RecordedQuery {
  text: string
  values: readonly unknown[]
}

function fakePool(rows: Record<string, unknown>[] = []) {
  const queries: RecordedQuery[] = []
  const pool = {
    async query(text: string, values: readonly unknown[]) {
      queries.push({ text, values })
      return { rows }
    },
    async end() {},
  } as unknown as Pool

  return { pool, queries }
}

test('hierarchy scopes every resource query to the server-derived organization', async () => {
  const { pool, queries } = fakePool()
  const repository = new PostgresOrganizationRepository(pool)
  const organizationId = '11111111-1111-4111-8111-111111111111'

  await repository.hierarchy(organizationId, { limit: 25 })

  assert.equal(queries.length, 3)
  for (const query of queries) {
    assert.match(query.text, /WHERE organization_id = \$1/)
    assert.deepEqual(query.values, [organizationId, 25])
  }
})

test('hierarchy applies the same validated status filter to every tenant-scoped query', async () => {
  const { pool, queries } = fakePool()
  const repository = new PostgresOrganizationRepository(pool)
  const organizationId = '22222222-2222-4222-8222-222222222222'

  await repository.hierarchy(organizationId, { limit: 10, status: 'active' })

  assert.equal(queries.length, 3)
  for (const query of queries) {
    assert.match(query.text, /organization_id = \$1 AND status = \$3/)
    assert.deepEqual(query.values, [organizationId, 10, 'active'])
  }
})

test('hierarchy maps database rows without leaking organization identifiers', async () => {
  const now = new Date('2026-09-19T00:00:00.000Z')
  const branchPool = {
    call: 0,
    async query() {
      this.call += 1
      if (this.call === 1) return { rows: [{ id: 'b1', name: 'HQ', code: 'HQ', status: 'active', created_at: now, updated_at: now }] }
      if (this.call === 2) return { rows: [{ id: 'd1', branch_id: 'b1', name: 'Operations', code: 'OPS', status: 'active', created_at: now, updated_at: now }] }
      return { rows: [{ id: 'p1', department_id: 'd1', name: 'Reviewer', description: null, status: 'active', created_at: now, updated_at: now }] }
    },
    async end() {},
  } as unknown as Pool

  const result = await new PostgresOrganizationRepository(branchPool).hierarchy('33333333-3333-4333-8333-333333333333', { limit: 50 })

  assert.deepEqual(result, {
    branches: [{ id: 'b1', name: 'HQ', code: 'HQ', status: 'active', createdAt: now.toISOString(), updatedAt: now.toISOString() }],
    departments: [{ id: 'd1', branchId: 'b1', name: 'Operations', code: 'OPS', status: 'active', createdAt: now.toISOString(), updatedAt: now.toISOString() }],
    personas: [{ id: 'p1', departmentId: 'd1', name: 'Reviewer', description: null, status: 'active', createdAt: now.toISOString(), updatedAt: now.toISOString() }],
  })
  assert.equal(JSON.stringify(result).includes('organizationId'), false)
})
