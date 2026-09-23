import { randomUUID } from 'node:crypto'
import { test, after } from 'node:test'
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

test('authenticated workflow API enforces tenant isolation, RBAC and the v1 list contract', { skip: !pool }, async () => {
  const suffix = `${Date.now()}-${randomUUID()}`
  const emailA = `api-security-a-${suffix}@test.invalid`
  const emailB = `api-security-b-${suffix}@test.invalid`
  const password = 'Strong-Test-Password-123!'
  const organizationMarker = `API Security ${suffix}`

  const registerA = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Tenant A Admin', email: emailA, password, organizationName: `${organizationMarker} A` } })
  assert.equal(registerA.statusCode, 201)
  const cookieA = cookieFrom(registerA)
  const sessionA = registerA.json().data

  const registerB = await app.inject({ method: 'POST', url: '/api/v1/auth/register', headers: { origin }, payload: { displayName: 'Tenant B Admin', email: emailB, password, organizationName: `${organizationMarker} B` } })
  assert.equal(registerB.statusCode, 201)
  const cookieB = cookieFrom(registerB)

  try {
    const createA = await app.inject({ method: 'POST', url: '/api/v1/workflows', headers: { origin, cookie: cookieA, 'idempotency-key': `api-security-key-a-${suffix}` }, payload: { name: 'Tenant A private workflow' } })
    assert.equal(createA.statusCode, 201)
    const workflowId = createA.json().data.id as string

    const crossTenantGet = await app.inject({ method: 'GET', url: `/api/v1/workflows/${workflowId}`, headers: { cookie: cookieB } })
    assert.equal(crossTenantGet.statusCode, 404)
    assert.equal(crossTenantGet.json().error.code, 'NOT_FOUND')

    const crossTenantList = await app.inject({ method: 'GET', url: '/api/v1/workflows?limit=50', headers: { cookie: cookieB } })
    assert.equal(crossTenantList.statusCode, 200)
    assert.deepEqual(crossTenantList.json().data, { items: [], nextCursor: null })

    const ownTenantList = await app.inject({ method: 'GET', url: '/api/v1/workflows?status=draft&limit=1', headers: { cookie: cookieA } })
    assert.equal(ownTenantList.statusCode, 200)
    assert.equal(ownTenantList.json().data.items.length, 1)
    assert.equal(ownTenantList.json().data.items[0].id, workflowId)
    assert.equal(ownTenantList.json().data.items[0].organizationId, sessionA.user.organizationId)
    assert.equal(ownTenantList.json().data.nextCursor, null)

    const malformedCursor = await app.inject({ method: 'GET', url: '/api/v1/workflows?cursor=not-a-valid-cursor', headers: { cookie: cookieA } })
    assert.equal(malformedCursor.statusCode, 400)
    assert.equal(malformedCursor.json().error.code, 'INVALID_CURSOR')

    const unsupportedTenantQuery = await app.inject({ method: 'GET', url: `/api/v1/workflows?organizationId=${sessionA.user.organizationId}`, headers: { cookie: cookieA } })
    assert.equal(unsupportedTenantQuery.statusCode, 400)
    assert.equal(unsupportedTenantQuery.json().error.code, 'VALIDATION_ERROR')

    const crossTenantPatch = await app.inject({ method: 'PATCH', url: `/api/v1/workflows/${workflowId}`, headers: { origin, cookie: cookieB }, payload: { expectedVersion: 1, name: 'Tenant B overwrite' } })
    assert.equal(crossTenantPatch.statusCode, 404)
    assert.equal(crossTenantPatch.json().error.code, 'NOT_FOUND')

    const roleUpdate = await pool!.query<{ role: string }>('UPDATE users SET role = \'viewer\' WHERE email = $1 RETURNING role', [emailB])
    assert.equal(roleUpdate.rowCount, 1)
    assert.equal(roleUpdate.rows[0]?.role, 'viewer')

    const viewerLogin = await app.inject({ method: 'POST', url: '/api/v1/auth/login', headers: { origin }, payload: { email: emailB, password } })
    assert.equal(viewerLogin.statusCode, 200)
    assert.equal(viewerLogin.json().data.user.role, 'viewer')
    const viewerCookie = cookieFrom(viewerLogin)

    const viewerList = await app.inject({ method: 'GET', url: '/api/v1/workflows', headers: { cookie: viewerCookie } })
    assert.equal(viewerList.statusCode, 200)
    assert.deepEqual(viewerList.json().data, { items: [], nextCursor: null })

    const viewerCreate = await app.inject({ method: 'POST', url: '/api/v1/workflows', headers: { origin, cookie: viewerCookie, 'idempotency-key': `api-security-key-b-${suffix}` }, payload: { name: 'Viewer must not create' } })
    assert.equal(viewerCreate.statusCode, 403)
    assert.equal(viewerCreate.json().error.code, 'FORBIDDEN')

    const unauthenticated = await app.inject({ method: 'GET', url: '/api/v1/workflows' })
    assert.equal(unauthenticated.statusCode, 401)
    assert.equal(unauthenticated.json().error.code, 'UNAUTHENTICATED')

    assert.equal(sessionA.user.organizationId, createA.json().data.organizationId)
  } finally {
    const ids = await pool!.query<{ id: string }>('SELECT id FROM organizations WHERE name LIKE $1', [`${organizationMarker}%`])
    const organizationIds = ids.rows.map((row) => row.id)
    if (organizationIds.length > 0) {
      await pool!.query('DELETE FROM audit_events WHERE organization_id = ANY($1::uuid[])', [organizationIds])
      await pool!.query('DELETE FROM workflow_idempotency_keys WHERE organization_id = ANY($1::uuid[])', [organizationIds])
      await pool!.query('DELETE FROM workflows WHERE organization_id = ANY($1::uuid[])', [organizationIds])
      await pool!.query('DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE organization_id = ANY($1::uuid[]))', [organizationIds])
      await pool!.query('DELETE FROM users WHERE organization_id = ANY($1::uuid[])', [organizationIds])
      await pool!.query('DELETE FROM organizations WHERE id = ANY($1::uuid[])', [organizationIds])
    }
  }
})

after(async () => {
  await app.close()
  await pool?.end()
})