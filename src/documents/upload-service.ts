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

/** Coordinates the provider-neutral binary upload boundary. */
export class DocumentUploadService {
  constructor(private readonly storage: DocumentObjectStorageLifecycle) {}

  async initiate(descriptor: DocumentUploadDescriptor, nowMs = Date.now()): Promise<InitiatedDocumentUpload> {
    assertUploadDescriptor(descriptor)
    const objectKey = createDocumentObjectKey(descriptor.organizationId, descriptor.documentId)
    const target = await this.storage.createUploadTarget(descriptor, objectKey)
    assertUploadTarget(descriptor, target, objectKey, nowMs)
    return { objectKey, target }
  }

  async verifyCompletion(descriptor: DocumentUploadDescriptor, objectKey: string): Promise<DocumentStoredObject> {
    assertUploadDescriptor(descriptor)
    const storedObject = await this.storage.inspectObject(objectKey)
    try {
      assertStoredObject(descriptor, storedObject, objectKey)
      return storedObject
    } catch (error) {
      if (storedObject) {
        try { await this.storage.deleteObject(objectKey) } catch {
          // Provider adapters report cleanup failures through telemetry;
          // completion remains failed with the original integrity error.
        }
      }
      throw error
    }
  }
}
