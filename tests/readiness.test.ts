import test from 'node:test'
import assert from 'node:assert/strict'
import { app } from '../src/app.js'

test('liveness endpoint remains independent from database readiness', async () => {
  const response = await app.inject({ method: 'GET', url: '/health', headers: { 'x-request-id': 'health-test' } })
  assert.equal(response.statusCode, 200)
  assert.equal(response.json().data.status, 'ok')
  assert.equal(response.json().meta.requestId, 'health-test')
})

test('readiness endpoint verifies the real database dependency', { skip: !process.env.DATABASE_URL }, async () => {
  const response = await app.inject({ method: 'GET', url: '/ready', headers: { 'x-request-id': 'ready-test' } })
  assert.equal(response.statusCode, 200)
  assert.equal(response.json().data.status, 'ready')
  assert.equal(response.json().data.dependencies.database, 'ok')
  assert.equal(response.json().meta.requestId, 'ready-test')
})

test.after(async () => {
  await app.close()
})
