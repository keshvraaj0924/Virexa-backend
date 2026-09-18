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

test('session inventory API is authenticated, user-scoped, current-aware, and non-cacheable', { skip: !pool }, async () => {
  const suffix = `${Date.now()}-${randomUUID()}`
  const password = 'Strong-Session-Inventory-Password-123!'
  const emailA = `inventory-api-a-${suffix}@test.invalid`
  const emailB = `inventory-api-b-${suffix}@test.invalid`
  const registerA = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Inventory A', email: emailA, password, organizationName: `Inventory API A ${suffix}` } })
  const registerB = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Inventory B', email: emailB, password, organizationName: `Inventory API B ${suffix}` } })
  assert.equal(registerA.statusCode, 201)
  assert.equal(registerB.statusCode, 201)
  const userA = registerA.json().data.user
  const userB = registerB.json().data.user
  const secondLoginA = await app.inject({ method: 'POST', url: '/api/v1/auth/login', headers: { origin }, payload: { email: emailA, password } })
  assert.equal(secondLoginA.statusCode, 200)
  const currentCookieA = cookieFrom(secondLoginA)

  try {
    const unauthenticated = await app.inject({ method: 'GET', url: '/api/v1/auth/sessions' })
    assert.equal(unauthenticated.statusCode, 401)

    const response = await app.inject({ method: 'GET', url: '/api/v1/auth/sessions', headers: { cookie: currentCookieA } })
    assert.equal(response.statusCode, 200)
    assert.match(String(response.headers['cache-control']), /no-store/i)
    const sessions = response.json().data.sessions
    assert.equal(sessions.length, 2)
    assert.equal(sessions.filter((session: { current: boolean }) => session.current).length, 1)
    assert.ok(sessions.every((session: Record<string, unknown>) => typeof session.id === 'string' && typeof session.createdAt === 'string' && typeof session.expiresAt === 'string'))
    assert.ok(sessions.every((session: Record<string, unknown>) => !('token' in session) && !('tokenDigest' in session) && !('userId' in session) && !('organizationId' in session)))

    const otherResponse = await app.inject({ method: 'GET', url: '/api/v1/auth/sessions', headers: { cookie: cookieFrom(registerB) } })
    assert.equal(otherResponse.statusCode, 200)
    const otherSessions = otherResponse.json().data.sessions
    assert.equal(otherSessions.length, 1)
    assert.ok(!sessions.some((session: { id: string }) => session.id === otherSessions[0].id))
  } finally {
    await pool!.query('DELETE FROM audit_events WHERE actor_user_id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM sessions WHERE user_id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM users WHERE id = ANY($1::uuid[])', [[userA.id, userB.id]])
    await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [[userA.organizationId, userB.organizationId]])
  }
})

after(async () => {
  await pool?.end()
})
