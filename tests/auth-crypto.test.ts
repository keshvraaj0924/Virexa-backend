import assert from 'node:assert/strict'
import test from 'node:test'
import { createSessionToken, hashPassword, verifyPassword } from '../src/auth/crypto.js'

test('password hashes verify without storing plaintext', async () => {
  const password = 'correct horse battery staple 2026'
  const encoded = await hashPassword(password)

  assert.notEqual(encoded, password)
  assert.equal(await verifyPassword(password, encoded), true)
  assert.equal(await verifyPassword('wrong password', encoded), false)
})

test('session tokens are unpredictable and unique', () => {
  const first = createSessionToken()
  const second = createSessionToken()

  assert.equal(first.length > 40, true)
  assert.notEqual(first, second)
})
