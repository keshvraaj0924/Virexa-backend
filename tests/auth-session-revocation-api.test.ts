import { randomUUID } from 'node:crypto'
import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { Pool } from 'pg'
import { app } from '../src/app.js'

const databaseUrl = process.env.DATABASE_URL
const pool = databaseUrl ? new Pool({ connectionString: databaseUrl, max: 4 }) : null
const origin = process.env.FRONTEND_ORIGIN ?? 'http://localhost:3000'

function cookieFrom(response: { headers: Record<string, unknown> }): string {
  const value = response.headers['set-cookie']
  const cookie = Array.isArray(value) ? value[0] : value
  assert.equal(typeof cookie, 'string')
  return cookie.split(';', 1)[0]
}

test('revoke-other-sessions API preserves current session, isolates users, and audits the action', { skip: !pool }, async () => {
  const suffix = `${Date.now()}-${randomUUID()}`
  const password = 'Strong-Session-Revocation-Password-123!'
  const emailA = `revoke-api-a-${suffix}@test.invalid`
  const emailB = `revoke-api-b-${suffix}@test.invalid`

  const registerA = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Session A', email: emailA, password, organizationName: `Revoke API A ${suffix}` } })
  const registerB = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Session B', email: emailB, password, organizationName: `Revoke API B ${suffix}` } })
  assert.equal(registerA.statusCode, 201)
  assert.equal(registerB.statusCode, 201)
  const firstCookieA = cookieFrom(registerA)
  const cookieB = cookieFrom(registerB)
  const userA = registerA.json().data.user
  const userB = registerB.json().data.user

  const secondLoginA = await app.inject({ method: 'POST', url: '/api/v1/auth/login', headers: { origin }, payload: { email: emailA, password } })
  assert.equal(secondLoginA.statusCode, 200)
  const currentCookieA = cookieFrom(secondLoginA)

  try {
    const response = await app.inject({ method: 'POST', url: '/api/v1/auth/sessions/revoke-others', headers: { origin, cookie: currentCookieA } })
    assert.equal(response.statusCode, 200)
    assert.equal(response.json().data.revokedCount, 1)

    const currentSession = await app.inject({ method: 'GET', url: '/api/v1/auth/session', headers: { cookie: currentCookieA } })
    assert.equal(currentSession.statusCode, 200)
    const oldSession = await app.inject({ method: 'GET', url: '/api/v1/auth/session', headers: { cookie: firstCookieA } })
    assert.equal(oldSession.statusCode, 401)
    const otherUserSession = await app.inject({ method: 'GET', url: '/api/v1/auth/session', headers: { cookie: cookieB } })
    assert.equal(otherUserSession.statusCode, 200)

    const audit = await pool!.query(`SELECT organization_id, actor_user_id, metadata FROM audit_events WHERE action = 'identity.other_sessions_revoked' AND actor_user_id = $1 ORDER BY created_at DESC LIMIT 1`, [userA.id])
    assert.equal(audit.rowCount, 1)
    assert.equal(audit.rows[0].organization_id, userA.organizationId)
    assert.equal(audit.rows[0].actor_user_id, userA.id)
    assert.equal(audit.rows[0].metadata.revokedCount, 1)
  } finally {
    await pool!.query('DELETE FROM audit_events WHERE actor_user_id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM sessions WHERE user_id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM users WHERE id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[userA.organizationId, userB.organizationId]])
  }
})

test('revoke-other-sessions API requires trusted origin and authentication', { skip: !pool }, async () => {
  const unauthenticated = await app.inject({ method: 'POST', url: '/api/v1/auth/sessions/revoke-others', headers: { origin } })
  assert.equal(unauthenticated.statusCode, 401)

  const untrusted = await app.inject({ method: 'POST', url: '/api/v1/auth/sessions/revoke-others', headers: { origin: 'https://attacker.invalid' } })
  assert.equal(untrusted.statusCode, 403)
})

after(async () => {
  await pool?.end()
})
