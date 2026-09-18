import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { randomUUID } from 'node:crypto'
import { PostgresAuthRepository } from '../src/auth/repository.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 8 }) : null

test('revoking other sessions preserves the current session and only affects its user', { skip: !pool }, async () => {
  const repository = new PostgresAuthRepository(pool!)
  const password = 'Session-revocation-test-password-1!'
  const primary = await repository.register({
    displayName: 'Primary Session User',
    email: `${randomUUID()}@test.invalid`,
    password,
    organizationName: `session-revocation-${randomUUID()}`,
  })
  const otherUser = await repository.register({
    displayName: 'Other Session User',
    email: `${randomUUID()}@test.invalid`,
    password,
    organizationName: `session-revocation-other-${randomUUID()}`,
  })

  try {
    const second = await repository.login(primary.user.email, password)
    const third = await repository.login(primary.user.email, password)
    assert.ok(second)
    assert.ok(third)

    const revokedCount = await repository.revokeOtherSessions(third.sessionToken)
    assert.equal(revokedCount, 2)
    assert.ok(await repository.getSession(third.sessionToken))
    assert.equal(await repository.getSession(primary.sessionToken), null)
    assert.equal(await repository.getSession(second.sessionToken), null)
    assert.ok(await repository.getSession(otherUser.sessionToken))
  } finally {
    await pool!.query('DELETE FROM sessions WHERE user_id = ANY($1::uuid[])', [[primary.user.id, otherUser.user.id]])
    await pool!.query('DELETE FROM users WHERE id = ANY($1::uuid[])', [[primary.user.id, otherUser.user.id]])
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[primary.user.organizationId, otherUser.user.organizationId]])
  }
})

after(async () => {
  await pool?.end()
})
