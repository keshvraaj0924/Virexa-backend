import { describe, expect, it } from 'vitest'
import { isRateLimitEnvelope } from './rate-limit-envelope.js'

describe('rate-limit envelope classification', () => {
  it('recognizes only the configured envelope for the current request', () => {
    expect(isRateLimitEnvelope({ error: { code: 'RATE_LIMITED', requestId: 'req-1' }, meta: { requestId: 'req-1' } }, 'req-1')).toBe(true)
  })
  it.each([
    null,
    new Error('RATE_LIMITED'),
    { error: { code: 'RATE_LIMITED', requestId: 'other' }, meta: { requestId: 'other' } },
    { error: { code: 'INTERNAL_ERROR', requestId: 'req-1' }, meta: { requestId: 'req-1' } },
    { error: { code: 'RATE_LIMITED', requestId: 'req-1' } },
    { error: { code: 'RATE_LIMITED', requestId: 'req-1' }, meta: { requestId: 'other' } },
  ])('does not translate unrelated errors into 429', (error) => {
    expect(isRateLimitEnvelope(error, 'req-1')).toBe(false)
  })
})
