import test from 'node:test'
import assert from 'node:assert/strict'
import { app } from '../src/app.js'

const request = {
  method: 'POST' as const,
  url: '/api/v1/auth/login',
  headers: {
    origin: 'http://localhost:3000',
    'content-type': 'application/json',
  },
  payload: JSON.stringify({}),
}

test('authentication endpoints enforce the configured request limit', async () => {
  for (let attempt = 1; attempt <= 10; attempt += 1) {
    const response = await app.inject(request)
    assert.equal(response.statusCode, 400, `attempt ${attempt} should reach request validation`)
  }

  const limited = await app.inject(request)
  assert.equal(limited.statusCode, 429)
  assert.equal(limited.json().error.code, 'RATE_LIMITED')
  assert.match(limited.headers['retry-after'] ?? '', /^\d+$/)
})

test.after(async () => {
  await app.close()
})
