import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { PostgresWorkflowRepository } from '../src/workflows/repository.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 4 }) : null

test('workflow repository never exposes one tenant resources to another tenant', { skip: !pool }, async () => {
  const organizationA = await pool!.query("INSERT INTO organizations (name) VALUES ('tenant-isolation-a') RETURNING id")
  const organizationB = await pool!.query("INSERT INTO organizations (name) VALUES ('tenant-isolation-b') RETURNING id")
  const organizationAId = organizationA.rows[0].id as string
  const organizationBId = organizationB.rows[0].id as string

  const userA = await pool!.query(
    "INSERT INTO users (organization_id, email, display_name, password_hash, role) VALUES ($1, $2, 'Tenant A', 'test-only-hash', 'admin') RETURNING id",
    [organizationAId, `${organizationAId}@test.invalid`],
  )
  const userB = await pool!.query(
    "INSERT INTO users (organization_id, email, display_name, password_hash, role) VALUES ($1, $2, 'Tenant B', 'test-only-hash', 'admin') RETURNING id",
    [organizationBId, `${organizationBId}@test.invalid`],
  )

  const userAId = userA.rows[0].id as string
  const userBId = userB.rows[0].id as string
  const repository = new PostgresWorkflowRepository(pool!)
  const sharedKey = 'tenant-scoped-idempotency-test-001'

  try {
    const workflowA = await repository.createIdempotent(
      organizationAId,
      userAId,
      { name: 'Tenant A workflow' },
      sharedKey,
    )

    assert.equal(await repository.getById(organizationBId, workflowA.workflow.id), null)
    assert.deepEqual(await repository.list(organizationBId, 50), [])
    assert.equal(
      await repository.update(organizationBId, workflowA.workflow.id, { name: 'Cross-tenant overwrite' }),
      null,
    )

    const workflowB = await repository.createIdempotent(
      organizationBId,
      userBId,
      { name: 'Tenant B workflow' },
      sharedKey,
    )

    assert.notEqual(workflowA.workflow.id, workflowB.workflow.id)
    assert.equal(await repository.getById(organizationAId, workflowB.workflow.id), null)
    assert.deepEqual((await repository.list(organizationAId, 50)).map((workflow) => workflow.id), [workflowA.workflow.id])
    assert.deepEqual((await repository.list(organizationBId, 50)).map((workflow) => workflow.id), [workflowB.workflow.id])
  } finally {
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[organizationAId, organizationBId]])
  }
})

after(async () => {
  await pool?.end()
})
