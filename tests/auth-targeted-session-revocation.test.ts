import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { randomUUID } from 'node:crypto'
import { PostgresAuthRepository } from '../src/auth/repository.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 8 }) : null

test('targeted session revocation is user-scoped, idempotent, and identifies current session', { skip: !pool }, async () => {
  const repository = new PostgresAuthRepository(pool!)
  const password = 'Targeted-session-revocation-test-1!'
  const primary = await repository.register({
    displayName: 'Target Session User',
    email: `${randomUUID()}@test.invalid`,
    password,
    organizationName: `target-session-${randomUUID()}`,
  })
  const otherUser = await repository.register({
    displayName: 'Other Target Session User',
    email: `${randomUUID()}@test.invalid`,
    password,
    organizationName: `target-session-other-${randomUUID()}`,
  })

  try {
    const second = await repository.login(primary.user.email, password)
    assert.ok(second)

    const primarySessions = await repository.listActiveSessions(second.sessionToken)
    const nonCurrent = primarySessions.find((session) => !session.current)
    assert.ok(nonCurrent)

    const otherSession = (await repository.listActiveSessions(otherUser.sessionToken))[0]
    const crossUserAttempt = await repository.revokeOwnedSession(second.sessionToken, otherSession.id)
    assert.deepEqual(crossUserAttempt, { revoked: false, current: false })
    assert.equal((await repository.listActiveSessions(otherUser.sessionToken)).length, 1)

    const revoked = await repository.revokeOwnedSession(second.sessionToken, nonCurrent.id)
    assert.deepEqual(revoked, { revoked: true, current: false })
    assert.equal((await repository.listActiveSessions(second.sessionToken)).length, 1)

    const replay = await repository.revokeOwnedSession(second.sessionToken, nonCurrent.id)
    assert.deepEqual(replay, { revoked: false, current: false })

    const current = (await repository.listActiveSessions(second.sessionToken))[0]
    assert.equal(current.current, true)
    const currentRevocation = await repository.revokeOwnedSession(second.sessionToken, current.id)
    assert.deepEqual(currentRevocation, { revoked: true, current: true })
    assert.equal(await repository.getSession(second.sessionToken), null)
  } finally {
    await pool!.query('DELETE FROM sessions WHERE user_id = ANY($1::uuid[])', [[primary.user.id, otherUser.user.id]])
    await pool!.query('DELETE FROM users WHERE id = ANY($1::uuid[])', [[primary.user.id, otherUser.user.id]])
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[primary.user.organizationId, otherUser.user.organizationId]])
  }
})

after(async () => {
  await pool?.end()
})
