import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { randomUUID } from 'node:crypto'
import { PostgresAuthRepository } from '../src/auth/repository.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 8 }) : null

test('active session inventory is scoped to the authenticated user and marks the current session', { skip: !pool }, async () => {
  const repository = new PostgresAuthRepository(pool!)
  const password = 'Session-inventory-test-password-1!'
  const primary = await repository.register({
    displayName: 'Session Inventory User',
    email: `${randomUUID()}@test.invalid`,
    password,
    organizationName: `session-inventory-${randomUUID()}`,
  })
  const otherUser = await repository.register({
    displayName: 'Other Inventory User',
    email: `${randomUUID()}@test.invalid`,
    password,
    organizationName: `session-inventory-other-${randomUUID()}`,
  })

  try {
    const second = await repository.login(primary.user.email, password)
    assert.ok(second)

    const sessions = await repository.listActiveSessions(second.sessionToken)
    assert.equal(sessions.length, 2)
    assert.equal(sessions.filter((session) => session.current).length, 1)
    assert.equal(sessions[0].current, true)
    assert.ok(sessions.every((session) => session.id && session.createdAt && session.expiresAt))

    const otherSessions = await repository.listActiveSessions(otherUser.sessionToken)
    assert.equal(otherSessions.length, 1)
    assert.equal(otherSessions[0].current, true)
    assert.ok(!sessions.some((session) => session.id === otherSessions[0].id))

    await repository.revokeOtherSessions(second.sessionToken)
    const afterRevocation = await repository.listActiveSessions(second.sessionToken)
    assert.equal(afterRevocation.length, 1)
    assert.equal(afterRevocation[0].current, true)
  } finally {
    await pool!.query('DELETE FROM sessions WHERE user_id = ANY($1::uuid[])', [[primary.user.id, otherUser.user.id]])
    await pool!.query('DELETE FROM users WHERE id = ANY($1::uuid[])', [[primary.user.id, otherUser.user.id]])
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[primary.user.organizationId, otherUser.user.organizationId]])
  }
})

after(async () => {
  await pool?.end()
})
