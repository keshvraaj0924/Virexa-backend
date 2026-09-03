import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { randomUUID } from 'node:crypto'
import { IdempotencyKeyReuseError, PostgresWorkflowRepository } from '../src/workflows/repository.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 16 }) : null

async function createFixture(name: string) {
  const organization = await pool!.query('INSERT INTO organizations (name) VALUES ($1) RETURNING id', [`${name}-${randomUUID()}`])
  const organizationId = organization.rows[0].id as string
  const user = await pool!.query(
    "INSERT INTO users (organization_id, email, display_name, password_hash, role) VALUES ($1, $2, 'Idempotency Test', 'test-only-hash', 'admin') RETURNING id",
    [organizationId, `${randomUUID()}@test.invalid`],
  )
  return { organizationId, userId: user.rows[0].id as string }
}

async function cleanupFixture(organizationId: string) {
  await pool!.query('DELETE FROM workflow_idempotency_keys WHERE organization_id = $1', [organizationId])
  await pool!.query('DELETE FROM workflows WHERE organization_id = $1', [organizationId])
  await pool!.query('DELETE FROM users WHERE organization_id = $1', [organizationId])
  await pool!.query('DELETE FROM organizations WHERE id = $1', [organizationId])
}

test('workflow creation is idempotent within a tenant and rejects key reuse with a different payload', { skip: !pool }, async () => {
  const { organizationId, userId } = await createFixture('idempotency-test')
  const repository = new PostgresWorkflowRepository(pool!)
  const key = `workflow-create-idempotency-${randomUUID()}`

  try {
    const first = await repository.createIdempotent(organizationId, userId, { name: 'Order intake' }, key)
    const replay = await repository.createIdempotent(organizationId, userId, { name: 'Order intake' }, key)

    assert.equal(first.replayed, false)
    assert.equal(replay.replayed, true)
    assert.equal(replay.workflow.id, first.workflow.id)

    await assert.rejects(
      repository.createIdempotent(organizationId, userId, { name: 'Different workflow' }, key),
      (error: unknown) => error instanceof IdempotencyKeyReuseError,
    )
  } finally {
    await cleanupFixture(organizationId)
  }
})

test('concurrent workflow creation with the same tenant key creates exactly one workflow', { skip: !pool }, async () => {
  const { organizationId, userId } = await createFixture('idempotency-concurrency-test')
  const repository = new PostgresWorkflowRepository(pool!)
  const key = `workflow-create-idempotency-concurrency-${randomUUID()}`

  try {
    const results = await Promise.allSettled(
      Array.from({ length: 8 }, () => repository.createIdempotent(organizationId, userId, { name: 'Concurrent intake' }, key)),
    )

    const rejected = results.filter((result): result is PromiseRejectedResult => result.status === 'rejected')
    assert.equal(rejected.length, 0, rejected.map((result) => String(result.reason)).join('\n'))

    const fulfilled = results.map((result) => {
      assert.equal(result.status, 'fulfilled')
      return result.value
    })
    assert.equal(fulfilled.filter((result) => !result.replayed).length, 1)
    assert.equal(fulfilled.filter((result) => result.replayed).length, 7)
    assert.equal(new Set(fulfilled.map((result) => result.workflow.id)).size, 1)

    const persisted = await pool!.query(
      'SELECT COUNT(*)::int AS count FROM workflows WHERE organization_id = $1',
      [organizationId],
    )
    assert.equal(persisted.rows[0].count, 1)
  } finally {
    await cleanupFixture(organizationId)
  }
})

after(async () => {
  await pool?.end()
})
