import test from 'node:test'
import assert from 'node:assert/strict'
import { assertProductionConfiguration, frontendOrigin } from '../src/config/runtime.js'

test('development defaults to the local frontend origin', () => {
  assert.equal(frontendOrigin({ NODE_ENV: 'development' }), 'http://localhost:3000')
})

test('production requires an explicit frontend origin', () => {
  assert.throws(
    () => frontendOrigin({ NODE_ENV: 'production' }),
    /FRONTEND_ORIGIN must be configured in production/,
  )
})

test('production rejects non-HTTPS frontend origins', () => {
  assert.throws(
    () => assertProductionConfiguration({ NODE_ENV: 'production', FRONTEND_ORIGIN: 'http://app.example.com' }),
    /FRONTEND_ORIGIN must use HTTPS in production/,
  )
})

test('production accepts a valid HTTPS frontend origin', () => {
  assert.doesNotThrow(() => assertProductionConfiguration({
    NODE_ENV: 'production',
    FRONTEND_ORIGIN: 'https://app.example.com',
  }))
})
