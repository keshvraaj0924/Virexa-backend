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

/**
 * Provider boundary for durable document binaries. Implementations must issue
 * short-lived, write-only upload targets and must never derive tenant scope
 * from caller-controlled object keys.
 */
export interface DocumentObjectStorage {
  createUploadTarget(descriptor: DocumentUploadDescriptor): Promise<DocumentUploadTarget>
}

const MAX_UPLOAD_TARGET_TTL_MS = 15 * 60 * 1000

/**
 * Generates an opaque object key under the server-authoritative organization
 * and document namespaces. The original filename is deliberately excluded so
 * user-controlled path segments can never influence storage location.
 */
export function createDocumentObjectKey(organizationId: string, documentId: string): string {
  const organizationNamespace = createHash('sha256').update(organizationId).digest('hex').slice(0, 32)
  return `documents/${organizationNamespace}/${documentId}/${randomUUID()}`
}

export function assertUploadDescriptor(descriptor: DocumentUploadDescriptor): void {
  if (!/^[0-9a-f]{64}$/.test(descriptor.checksumSha256)) {
    throw new Error('Document checksum must be a lowercase SHA-256 digest')
  }
  if (!Number.isSafeInteger(descriptor.sizeBytes) || descriptor.sizeBytes <= 0) {
    throw new Error('Document size must be a positive safe integer')
  }
  if (!descriptor.mediaType.trim()) throw new Error('Document media type is required')
  if (!descriptor.originalFileName.trim()) throw new Error('Document filename is required')
}

/**
 * Validates provider output before an upload target is returned to a caller.
 * The API layer must provide the expected object key generated from trusted
 * organization/document identity; provider output cannot redirect a tenant to
 * a different object namespace or relax immutable integrity metadata.
 */
export function assertUploadTarget(
  descriptor: DocumentUploadDescriptor,
  target: DocumentUploadTarget,
  expectedObjectKey: string,
  nowMs = Date.now(),
): void {
  if (target.objectKey !== expectedObjectKey) throw new Error('Document upload target object key does not match the trusted namespace')

  let uploadUrl: URL
  try {
    uploadUrl = new URL(target.uploadUrl)
  } catch {
    throw new Error('Document upload target URL is invalid')
  }
  if (uploadUrl.protocol !== 'https:') throw new Error('Document upload target URL must use HTTPS')

  const expiresAtMs = Date.parse(target.expiresAt)
  if (!Number.isFinite(expiresAtMs) || expiresAtMs <= nowMs || expiresAtMs > nowMs + MAX_UPLOAD_TARGET_TTL_MS) {
    throw new Error('Document upload target expiry is invalid')
  }

  const headers = Object.fromEntries(Object.entries(target.requiredHeaders).map(([name, value]) => [name.toLowerCase(), value]))
  if (headers['content-type'] !== descriptor.mediaType) throw new Error('Document upload target must bind content type')
  if (headers['content-length'] !== String(descriptor.sizeBytes)) throw new Error('Document upload target must bind content length')
  if (headers['x-virexa-sha256'] !== descriptor.checksumSha256) throw new Error('Document upload target must bind SHA-256 integrity metadata')
}
