import { describe, expect, it } from 'vitest'
import { assertUploadDescriptor, createDocumentObjectKey } from './storage.js'

describe('document object storage boundary', () => {
  it('namespaces object keys by a non-reversible organization digest and document id', () => {
    const organizationId = '11111111-1111-4111-8111-111111111111'
    const documentId = '22222222-2222-4222-8222-222222222222'
    const key = createDocumentObjectKey(organizationId, documentId)

    expect(key).toMatch(/^documents\/[0-9a-f]{32}\/22222222-2222-4222-8222-222222222222\/[0-9a-f-]{36}$/)
    expect(key).not.toContain(organizationId)
  })

  it('produces different storage namespaces for different organizations', () => {
    const documentId = '22222222-2222-4222-8222-222222222222'
    const first = createDocumentObjectKey('11111111-1111-4111-8111-111111111111', documentId)
    const second = createDocumentObjectKey('33333333-3333-4333-8333-333333333333', documentId)

    expect(first.split('/')[1]).not.toBe(second.split('/')[1])
  })

  it('never includes caller-controlled filenames in object keys', () => {
    const key = createDocumentObjectKey(
      '11111111-1111-4111-8111-111111111111',
      '22222222-2222-4222-8222-222222222222',
    )

    expect(key).not.toContain('..')
    expect(key).not.toContain('invoice')
  })

  it('rejects unsafe upload descriptors before a provider is invoked', () => {
    expect(() => assertUploadDescriptor({
      organizationId: '11111111-1111-4111-8111-111111111111',
      documentId: '22222222-2222-4222-8222-222222222222',
      originalFileName: 'invoice.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 0,
      checksumSha256: 'invalid',
    })).toThrow()
  })

  it('accepts a valid immutable upload descriptor', () => {
    expect(() => assertUploadDescriptor({
      organizationId: '11111111-1111-4111-8111-111111111111',
      documentId: '22222222-2222-4222-8222-222222222222',
      originalFileName: 'invoice.pdf',
      mediaType: 'application/pdf',
      sizeBytes: 1024,
      checksumSha256: 'a'.repeat(64),
    })).not.toThrow()
  })
})
