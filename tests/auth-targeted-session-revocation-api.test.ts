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

async function activeSessions(cookie: string) {
  const response = await app.inject({ method: 'GET', url: '/api/v1/auth/sessions', headers: { cookie } })
  assert.equal(response.statusCode, 200)
  return response.json().data.sessions as Array<{ id: string; current: boolean }>
}

test('targeted revocation is owner-scoped, auditable, and clears the current cookie', { skip: !pool }, async () => {
  const suffix = `${Date.now()}-${randomUUID()}`
  const password = 'Strong-Targeted-Revocation-Password-123!'
  const emailA = `target-api-a-${suffix}@test.invalid`
  const emailB = `target-api-b-${suffix}@test.invalid`
  const registerA = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Target A', email: emailA, password, organizationName: `Target API A ${suffix}` } })
  const registerB = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Target B', email: emailB, password, organizationName: `Target API B ${suffix}` } })
  assert.equal(registerA.statusCode, 201)
  assert.equal(registerB.statusCode, 201)
  const oldCookieA = cookieFrom(registerA)
  const cookieB = cookieFrom(registerB)
  const userA = registerA.json().data.user
  const userB = registerB.json().data.user
  const secondLoginA = await app.inject({ method: 'POST', url: '/api/v1/auth/login', headers: { origin }, payload: { email: emailA, password } })
  assert.equal(secondLoginA.statusCode, 200)
  const currentCookieA = cookieFrom(secondLoginA)

  try {
    const sessionsA = await activeSessions(currentCookieA)
    const sessionsB = await activeSessions(cookieB)
    const oldA = sessionsA.find((session) => !session.current)
    const currentA = sessionsA.find((session) => session.current)
    assert.ok(oldA)
    assert.ok(currentA)
    assert.equal(sessionsB.length, 1)

    const crossUser = await app.inject({ method: 'DELETE', url: `/api/v1/auth/sessions/${sessionsB[0].id}`, headers: { origin, cookie: currentCookieA } })
    assert.equal(crossUser.statusCode, 404)
    assert.equal((await app.inject({ method: 'GET', url: '/api/v1/auth/session', headers: { cookie: cookieB } })).statusCode, 200)

    const revokeOld = await app.inject({ method: 'DELETE', url: `/api/v1/auth/sessions/${oldA.id}`, headers: { origin, cookie: currentCookieA } })
    assert.equal(revokeOld.statusCode, 200)
    assert.deepEqual(revokeOld.json().data, { revoked: true, current: false })
    assert.match(String(revokeOld.headers['cache-control']), /no-store/)
    assert.equal((await app.inject({ method: 'GET', url: '/api/v1/auth/session', headers: { cookie: oldCookieA } })).statusCode, 401)

    const replay = await app.inject({ method: 'DELETE', url: `/api/v1/auth/sessions/${oldA.id}`, headers: { origin, cookie: currentCookieA } })
    assert.equal(replay.statusCode, 404)

    const revokeCurrent = await app.inject({ method: 'DELETE', url: `/api/v1/auth/sessions/${currentA.id}`, headers: { origin, cookie: currentCookieA } })
    assert.equal(revokeCurrent.statusCode, 200)
    assert.deepEqual(revokeCurrent.json().data, { revoked: true, current: true })
    assert.match(String(revokeCurrent.headers['set-cookie']), /virexa_session=;/)
    assert.equal((await app.inject({ method: 'GET', url: '/api/v1/auth/session', headers: { cookie: currentCookieA } })).statusCode, 401)

    const audit = await pool!.query(`SELECT organization_id, actor_user_id, resource_id, metadata FROM audit_events WHERE action = 'identity.session_revoked' AND actor_user_id = $1 ORDER BY created_at`, [userA.id])
    assert.equal(audit.rowCount, 2)
    assert.ok(audit.rows.every((row) => row.organization_id === userA.organizationId && row.actor_user_id === userA.id))
    assert.deepEqual(audit.rows.map((row) => row.resource_id), [oldA.id, currentA.id])
    assert.deepEqual(audit.rows.map((row) => row.metadata.current), [false, true])
  } finally {
    await pool!.query('DELETE FROM audit_events WHERE actor_user_id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM sessions WHERE user_id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM users WHERE id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[userA.organizationId, userB.organizationId]])
  }
})

test('targeted revocation validates UUID, trusted origin, and authentication', { skip: !pool }, async () => {
  const unauthenticated = await app.inject({ method: 'DELETE', url: `/api/v1/auth/sessions/${randomUUID()}`, headers: { origin } })
  assert.equal(unauthenticated.statusCode, 401)
  const untrusted = await app.inject({ method: 'DELETE', url: `/api/v1/auth/sessions/${randomUUID()}`, headers: { origin: 'https://attacker.invalid' } })
  assert.equal(untrusted.statusCode, 403)
})

after(async () => {
  await pool?.end()
})
