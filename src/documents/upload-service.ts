import {
  assertStoredObject,
  assertUploadDescriptor,
  assertUploadTarget,
  createDocumentObjectKey,
  type DocumentObjectStorageLifecycle,
  type DocumentStoredObject,
  type DocumentUploadDescriptor,
  type DocumentUploadTarget,
} from './storage.js'

export interface InitiatedDocumentUpload {
  objectKey: string
  target: DocumentUploadTarget
}

/**
 * Coordinates the provider-neutral binary upload boundary. Tenant and document
 * identity must be supplied from server-authoritative state; callers never
 * choose an object key. Persistence/state transitions remain the responsibility
 * of the document application service so this component stays provider-neutral.
 */
export class DocumentUploadService {
  constructor(private readonly storage: DocumentObjectStorageLifecycle) {}

  async initiate(descriptor: DocumentUploadDescriptor, nowMs = Date.now()): Promise<InitiatedDocumentUpload> {
    assertUploadDescriptor(descriptor)
    const objectKey = createDocumentObjectKey(descriptor.organizationId, descriptor.documentId)
    const target = await this.storage.createUploadTarget(descriptor)
    assertUploadTarget(descriptor, target, objectKey, nowMs)
    return { objectKey, target }
  }

  async verifyCompletion(
    descriptor: DocumentUploadDescriptor,
    objectKey: string,
  ): Promise<DocumentStoredObject> {
    assertUploadDescriptor(descriptor)
    const storedObject = await this.storage.inspectObject(objectKey)

    try {
      assertStoredObject(descriptor, storedObject, objectKey)
      return storedObject
    } catch (error) {
      // Cleanup is compensating and best-effort. Never replace the integrity
      // failure with a provider cleanup failure, which would obscure the cause.
      if (storedObject) {
        try {
          await this.storage.deleteObject(objectKey)
        } catch {
          // Operational adapters must surface cleanup failures through their
          // own telemetry; completion still fails closed here.
        }
      }
      throw error
    }
  }
}
