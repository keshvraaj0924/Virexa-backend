import { describe, expect, it } from 'vitest'
import {
  assertStoredObject,
  assertUploadTarget,
  type DocumentObjectStorage,
  type DocumentStoredObject,
  type DocumentUploadDescriptor,
  type DocumentUploadTarget,
} from './storage.js'

const descriptor: DocumentUploadDescriptor = {
  organizationId: '11111111-1111-4111-8111-111111111111',
  documentId: '22222222-2222-4222-8222-222222222222',
  originalFileName: 'invoice.pdf',
  mediaType: 'application/pdf',
  sizeBytes: 1024,
  checksumSha256: 'a'.repeat(64),
}

const nowMs = Date.parse('2026-09-19T12:00:00.000Z')
const expectedObjectKey = 'documents/trusted-tenant/document/object'

function target(overrides: Partial<DocumentUploadTarget> = {}): DocumentUploadTarget {
  return {
    objectKey: expectedObjectKey,
    uploadUrl: 'https://storage.example/upload',
    expiresAt: new Date(nowMs + 60_000).toISOString(),
    requiredHeaders: {
      'content-type': descriptor.mediaType,
      'content-length': String(descriptor.sizeBytes),
      'x-virexa-sha256': descriptor.checksumSha256,
    },
    ...overrides,
  }
}

function storedObject(overrides: Partial<DocumentStoredObject> = {}): DocumentStoredObject {
  return {
    objectKey: expectedObjectKey,
    mediaType: descriptor.mediaType,
    sizeBytes: descriptor.sizeBytes,
    checksumSha256: descriptor.checksumSha256,
    ...overrides,
  }
}

describe('DocumentObjectStorage lifecycle contract', () => {
  it('requires upload targets to expire and bind immutable integrity metadata', async () => {
    const issuedTarget = target()
    const storage: DocumentObjectStorage = {
      async createUploadTarget(received, objectKey) {
        expect(received).toEqual(descriptor)
        expect(objectKey).toBe(expectedObjectKey)
        return issuedTarget
      },
    }

    const issued = await storage.createUploadTarget(descriptor, expectedObjectKey)
    expect(() => assertUploadTarget(descriptor, issued, expectedObjectKey, nowMs)).not.toThrow()
  })

  it('rejects provider output that escapes the trusted tenant/object namespace', () => {
    expect(() => assertUploadTarget(descriptor, target({ objectKey: 'documents/other-tenant/document/object' }), expectedObjectKey, nowMs))
      .toThrow(/trusted namespace/)
  })

  it('rejects insecure, expired, or excessively long-lived upload targets', () => {
    expect(() => assertUploadTarget(descriptor, target({ uploadUrl: 'http://storage.example/upload' }), expectedObjectKey, nowMs)).toThrow(/HTTPS/)
    expect(() => assertUploadTarget(descriptor, target({ expiresAt: new Date(nowMs - 1).toISOString() }), expectedObjectKey, nowMs)).toThrow(/expiry/)
    expect(() => assertUploadTarget(descriptor, target({ expiresAt: new Date(nowMs + 16 * 60_000).toISOString() }), expectedObjectKey, nowMs)).toThrow(/expiry/)
  })

  it('rejects upload targets that weaken immutable integrity bindings', () => {
    expect(() => assertUploadTarget(descriptor, target({ requiredHeaders: { ...target().requiredHeaders, 'content-length': '2048' } }), expectedObjectKey, nowMs)).toThrow(/content length/)
    expect(() => assertUploadTarget(descriptor, target({ requiredHeaders: { ...target().requiredHeaders, 'x-virexa-sha256': 'b'.repeat(64) } }), expectedObjectKey, nowMs)).toThrow(/SHA-256/)
  })

  it('accepts completion only from provider-observed immutable object metadata', () => {
    expect(() => assertStoredObject(descriptor, storedObject(), expectedObjectKey)).not.toThrow()
  })

  it('fails completion closed when the object is absent or outside the trusted namespace', () => {
    expect(() => assertStoredObject(descriptor, null, expectedObjectKey)).toThrow(/not available/)
    expect(() => assertStoredObject(descriptor, storedObject({ objectKey: 'documents/other-tenant/document/object' }), expectedObjectKey)).toThrow(/trusted namespace/)
  })

  it('rejects completion when provider-observed integrity metadata differs from intake', () => {
    expect(() => assertStoredObject(descriptor, storedObject({ mediaType: 'application/octet-stream' }), expectedObjectKey)).toThrow(/content type/)
    expect(() => assertStoredObject(descriptor, storedObject({ sizeBytes: 2048 }), expectedObjectKey)).toThrow(/content length/)
    expect(() => assertStoredObject(descriptor, storedObject({ checksumSha256: 'b'.repeat(64) }), expectedObjectKey)).toThrow(/SHA-256/)
  })
})