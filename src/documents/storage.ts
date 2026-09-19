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
