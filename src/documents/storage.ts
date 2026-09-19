import { createHash, randomUUID } from 'node:crypto'

export interface DocumentUploadDescriptor {
  organizationId: string
  documentId: string
  originalFileName: string
  mediaType: string
  sizeBytes: number
  checksumSha256: string
}

export interface DocumentUploadTarget {
  objectKey: string
  uploadUrl: string
  expiresAt: string
  requiredHeaders: Readonly<Record<string, string>>
}

export interface DocumentStoredObject {
  objectKey: string
  mediaType: string
  sizeBytes: number
  checksumSha256: string
}

/** Provider boundary for issuing durable document uploads. */
export interface DocumentObjectStorage {
  createUploadTarget(descriptor: DocumentUploadDescriptor, objectKey: string): Promise<DocumentUploadTarget>
}

export interface DocumentObjectStorageLifecycle extends DocumentObjectStorage {
  inspectObject(objectKey: string): Promise<DocumentStoredObject | null>
  deleteObject(objectKey: string): Promise<void>
}

const MAX_UPLOAD_TARGET_TTL_MS = 15 * 60 * 1000

export function createDocumentObjectKey(organizationId: string, documentId: string): string {
  const organizationNamespace = createHash('sha256').update(organizationId).digest('hex').slice(0, 32)
  return `documents/${organizationNamespace}/${documentId}/${randomUUID()}`
}

export function assertUploadDescriptor(descriptor: DocumentUploadDescriptor): void {
  if (!/^[0-9a-f]{64}$/.test(descriptor.checksumSha256)) throw new Error('Document checksum must be a lowercase SHA-256 digest')
  if (!Number.isSafeInteger(descriptor.sizeBytes) || descriptor.sizeBytes <= 0) throw new Error('Document size must be a positive safe integer')
  if (!descriptor.mediaType.trim()) throw new Error('Document media type is required')
  if (!descriptor.originalFileName.trim()) throw new Error('Document filename is required')
}

export function assertUploadTarget(descriptor: DocumentUploadDescriptor, target: DocumentUploadTarget, expectedObjectKey: string, nowMs = Date.now()): void {
  if (target.objectKey !== expectedObjectKey) throw new Error('Document upload target object key does not match the trusted namespace')
  let uploadUrl: URL
  try { uploadUrl = new URL(target.uploadUrl) } catch { throw new Error('Document upload target URL is invalid') }
  if (uploadUrl.protocol !== 'https:') throw new Error('Document upload target URL must use HTTPS')
  const expiresAtMs = Date.parse(target.expiresAt)
  if (!Number.isFinite(expiresAtMs) || expiresAtMs <= nowMs || expiresAtMs > nowMs + MAX_UPLOAD_TARGET_TTL_MS) throw new Error('Document upload target expiry is invalid')
  const headers = Object.fromEntries(Object.entries(target.requiredHeaders).map(([name, value]) => [name.toLowerCase(), value]))
  if (headers['content-type'] !== descriptor.mediaType) throw new Error('Document upload target must bind content type')
  if (headers['content-length'] !== String(descriptor.sizeBytes)) throw new Error('Document upload target must bind content length')
  if (headers['x-virexa-sha256'] !== descriptor.checksumSha256) throw new Error('Document upload target must bind SHA-256 integrity metadata')
}

export function assertStoredObject(descriptor: DocumentUploadDescriptor, storedObject: DocumentStoredObject | null, expectedObjectKey: string): asserts storedObject is DocumentStoredObject {
  if (!storedObject) throw new Error('Document object is not available in durable storage')
  if (storedObject.objectKey !== expectedObjectKey) throw new Error('Stored document object key does not match the trusted namespace')
  if (storedObject.mediaType !== descriptor.mediaType) throw new Error('Stored document content type does not match intake metadata')
  if (storedObject.sizeBytes !== descriptor.sizeBytes) throw new Error('Stored document content length does not match intake metadata')
  if (storedObject.checksumSha256 !== descriptor.checksumSha256) throw new Error('Stored document SHA-256 does not match intake metadata')
}
