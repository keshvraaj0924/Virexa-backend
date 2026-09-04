import { describe, expect, it } from 'vitest'
import { assertProductionConfiguration, frontendOrigin, trustProxyHops } from './runtime.js'

describe('runtime configuration', () => {
  it('defaults trusted proxy hops to zero', () => {
    expect(trustProxyHops({ NODE_ENV: 'development' })).toBe(0)
  })

  it('parses a configured trusted proxy hop count', () => {
    expect(trustProxyHops({ NODE_ENV: 'production', TRUST_PROXY_HOPS: '2' })).toBe(2)
  })

  it('rejects malformed trusted proxy hop counts', () => {
    expect(() => trustProxyHops({ NODE_ENV: 'production', TRUST_PROXY_HOPS: '-1' })).toThrow('TRUST_PROXY_HOPS must be a non-negative integer')
    expect(() => trustProxyHops({ NODE_ENV: 'production', TRUST_PROXY_HOPS: 'proxy' })).toThrow('TRUST_PROXY_HOPS must be a non-negative integer')
    expect(() => trustProxyHops({ NODE_ENV: 'production', TRUST_PROXY_HOPS: '11' })).toThrow('TRUST_PROXY_HOPS must be between 0 and 10')
  })

  it('keeps the development frontend origin default', () => {
    expect(frontendOrigin({ NODE_ENV: 'development' })).toBe('http://localhost:3000')
  })

  it('requires a secure frontend origin in production', () => {
    expect(() => assertProductionConfiguration({ NODE_ENV: 'production' })).toThrow('FRONTEND_ORIGIN must be configured in production')
    expect(() => assertProductionConfiguration({ NODE_ENV: 'production', FRONTEND_ORIGIN: 'http://app.example.com' })).toThrow('FRONTEND_ORIGIN must use HTTPS in production')
  })
})
