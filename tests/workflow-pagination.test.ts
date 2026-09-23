import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { InvalidWorkflowCursorError, PostgresWorkflowRepository } from '../src/workflows/repository.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 4 }) : null

test('workflow cursor pagination is deterministic, tenant scoped, filterable and rejects malformed cursors', { skip: !pool }, async () => {
  const organizationA = await pool!.query("INSERT INTO organizations (name) VALUES ('workflow-pagination-a') RETURNING id")
  const organizationB = await pool!.query("INSERT INTO organizations (name) VALUES ('workflow-pagination-b') RETURNING id")
  const organizationAId = organizationA.rows[0].id as string
  const organizationBId = organizationB.rows[0].id as string

  const userA = await pool!.query(
    "INSERT INTO users (organization_id, email, display_name, password_hash, role) VALUES ($1, $2, 'Pagination A', 'test-only-hash', 'admin') RETURNING id",
    [organizationAId, `${organizationAId}@test.invalid`],
  )
  const userB = await pool!.query(
    "INSERT INTO users (organization_id, email, display_name, password_hash, role) VALUES ($1, $2, 'Pagination B', 'test-only-hash', 'admin') RETURNING id",
    [organizationBId, `${organizationBId}@test.invalid`],
  )

  const repository = new PostgresWorkflowRepository(pool!)
  const userAId = userA.rows[0].id as string
  const userBId = userB.rows[0].id as string

  try {
    const first = await repository.create(organizationAId, userAId, { name: 'First workflow' })
    const second = await repository.create(organizationAId, userAId, { name: 'Second workflow' })
    const third = await repository.create(organizationAId, userAId, { name: 'Third workflow' })
    await repository.create(organizationBId, userBId, { name: 'Other tenant workflow' })

    const activated = await repository.update(
      organizationAId,
      second.id,
      { expectedVersion: second.version, status: 'active' },
      'draft',
    )
    assert.equal(activated?.status, 'active')

    const pageOne = await repository.listPage(organizationAId, { limit: 2 })
    assert.equal(pageOne.items.length, 2)
    assert.ok(pageOne.nextCursor)
    assert.ok(pageOne.items.every((workflow) => workflow.organizationId === organizationAId))

    const pageTwo = await repository.listPage(organizationAId, { limit: 2, cursor: pageOne.nextCursor! })
    assert.equal(pageTwo.items.length, 1)
    assert.equal(pageTwo.nextCursor, null)

    const pagedIds = [...pageOne.items, ...pageTwo.items].map((workflow) => workflow.id)
    assert.equal(new Set(pagedIds).size, 3)
    assert.deepEqual(new Set(pagedIds), new Set([first.id, second.id, third.id]))

    const activePage = await repository.listPage(organizationAId, { limit: 10, status: 'active' })
    assert.deepEqual(activePage.items.map((workflow) => workflow.id), [second.id])
    assert.equal(activePage.nextCursor, null)

    await assert.rejects(
      () => repository.listPage(organizationAId, { limit: 10, cursor: 'not-a-valid-workflow-cursor' }),
      InvalidWorkflowCursorError,
    )
  } finally {
    await pool!.query('DELETE FROM workflow_idempotency_keys WHERE organization_id = ANY($1::uuid[])', [[organizationAId, organizationBId]])
    await pool!.query('DELETE FROM workflows WHERE organization_id = ANY($1::uuid[])', [[organizationAId, organizationBId]])
    await pool!.query('DELETE FROM users WHERE organization_id = ANY($1::uuid[])', [[organizationAId, organizationBId]])
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[organizationAId, organizationBId]])
  }
})

after(async () => {
  await pool?.end()
})
