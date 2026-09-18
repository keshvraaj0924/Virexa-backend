import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { randomUUID } from 'node:crypto'
import { PostgresAuthRepository } from '../src/auth/repository.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 16 }) : null

test('concurrent logins preserve the active-session cap', { skip: !pool }, async () => {
  const repository = new PostgresAuthRepository(pool!)
  const email = `${randomUUID()}@test.invalid`
  const password = 'Concurrent-session-test-password-1!'
  const registration = await repository.register({
    displayName: 'Concurrent Session Test',
    email,
    password,
    organizationName: `session-concurrency-${randomUUID()}`,
  })

  try {
    const attempts = await Promise.all(
      Array.from({ length: 8 }, () => repository.login(email, password)),
    )
    assert.equal(attempts.every((attempt) => attempt !== null), true)

    const active = await pool!.query(
      `SELECT COUNT(*)::int AS count
       FROM sessions
       WHERE user_id = $1 AND revoked_at IS NULL AND expires_at > now()`,
      [registration.user.id],
    )
    assert.equal(active.rows[0].count, 5)
  } finally {
    await pool!.query('DELETE FROM sessions WHERE user_id = $1', [registration.user.id])
    await pool!.query('DELETE FROM users WHERE id = $1', [registration.user.id])
    await pool!.query('DELETE FROM organizations WHERE id = $1', [registration.user.organizationId])
  }
})

after(async () => {
  await pool?.end()
})
