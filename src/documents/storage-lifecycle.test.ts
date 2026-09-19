import { describe, expect, it } from 'vitest'
import type { DocumentObjectStorage, DocumentUploadDescriptor, DocumentUploadTarget } from './storage.js'

const descriptor: DocumentUploadDescriptor = {
  organizationId: '11111111-1111-4111-8111-111111111111',
  documentId: '22222222-2222-4222-8222-222222222222',
  originalFileName: 'invoice.pdf',
  mediaType: 'application/pdf',
  sizeBytes: 1024,
  checksumSha256: 'a'.repeat(64),
}

describe('DocumentObjectStorage lifecycle contract', () => {
  it('requires upload targets to expire and bind immutable integrity metadata', async () => {
    const target: DocumentUploadTarget = {
      objectKey: 'documents/tenant/document/object',
      uploadUrl: 'https://storage.example/upload',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
      requiredHeaders: {
        'content-type': descriptor.mediaType,
        'content-length': String(descriptor.sizeBytes),
        'x-virexa-sha256': descriptor.checksumSha256,
      },
    }
    const storage: DocumentObjectStorage = {
      async createUploadTarget(received) {
        expect(received).toEqual(descriptor)
        return target
      },
    }

    const issued = await storage.createUploadTarget(descriptor)
    expect(Date.parse(issued.expiresAt)).toBeGreaterThan(Date.now())
    expect(issued.requiredHeaders['content-type']).toBe(descriptor.mediaType)
    expect(issued.requiredHeaders['content-length']).toBe(String(descriptor.sizeBytes))
    expect(issued.requiredHeaders['x-virexa-sha256']).toBe(descriptor.checksumSha256)
  })
})
