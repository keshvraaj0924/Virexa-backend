import { describe, expect, it } from 'vitest'
import { S3DocumentObjectStorage } from './s3-storage.js'

describe('S3DocumentObjectStorage configuration', () => {
  it('requires an explicit bucket and region', () => {
    expect(() => new S3DocumentObjectStorage({ bucket: '   ', region: 'us-east-1' }))
      .toThrow(/bucket/i)
    expect(() => new S3DocumentObjectStorage({ bucket: 'virexa-documents', region: '   ' }))
      .toThrow(/region/i)
  })

  it('rejects insecure custom endpoints', () => {
    expect(() => new S3DocumentObjectStorage({
      bucket: 'virexa-documents',
      region: 'us-east-1',
      endpoint: 'http://storage.internal.example',
    })).toThrow(/HTTPS/)
  })

  it('accepts HTTPS S3-compatible endpoints without static credentials', () => {
    expect(() => new S3DocumentObjectStorage({
      bucket: 'virexa-documents',
      region: 'us-east-1',
      endpoint: 'https://storage.internal.example',
      forcePathStyle: true,
    })).not.toThrow()
  })

  it('bounds signed upload targets to fifteen minutes', () => {
    expect(() => new S3DocumentObjectStorage({
      bucket: 'virexa-documents',
      region: 'us-east-1',
      uploadTtlSeconds: 0,
    })).toThrow(/between 1 and 900 seconds/)

    expect(() => new S3DocumentObjectStorage({
      bucket: 'virexa-documents',
      region: 'us-east-1',
      uploadTtlSeconds: 901,
    })).toThrow(/between 1 and 900 seconds/)

    expect(() => new S3DocumentObjectStorage({
      bucket: 'virexa-documents',
      region: 'us-east-1',
      uploadTtlSeconds: 900,
    })).not.toThrow()
  })
})
