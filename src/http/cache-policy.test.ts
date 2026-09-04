import { describe, expect, it, vi } from 'vitest'
import { markSensitiveResponse } from './cache-policy.js'

describe('markSensitiveResponse', () => {
  it('applies a private no-store policy to authenticated data responses', () => {
    const reply = {
      header: vi.fn(),
    }

    markSensitiveResponse(reply as never)

    expect(reply.header).toHaveBeenNthCalledWith(1, 'Cache-Control', 'private, no-store, max-age=0')
    expect(reply.header).toHaveBeenNthCalledWith(2, 'Pragma', 'no-cache')
    expect(reply.header).toHaveBeenNthCalledWith(3, 'Expires', '0')
    expect(reply.header).toHaveBeenCalledTimes(3)
  })
})
