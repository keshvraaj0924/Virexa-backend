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
import type {
  DocumentUploadAttempt,
  DocumentUploadAttemptRepository,
} from './upload-attempt-repository.js'

export interface InitiatedDocumentUpload {
  objectKey: string
  target: DocumentUploadTarget
}

export interface PersistedDocumentUpload extends InitiatedDocumentUpload {
  attempt: DocumentUploadAttempt
  replayed: boolean
}

export interface CompletedDocumentUpload {
  attempt: DocumentUploadAttempt
  storedObject: DocumentStoredObject
  replayed: boolean
}

/** Coordinates the provider-neutral binary upload boundary. */
export class DocumentUploadService {
  constructor(
    private readonly storage: DocumentObjectStorageLifecycle,
    private readonly attempts?: DocumentUploadAttemptRepository,
  ) {}

  async initiate(descriptor: DocumentUploadDescriptor, nowMs = Date.now()): Promise<InitiatedDocumentUpload> {
    assertUploadDescriptor(descriptor)
    const objectKey = createDocumentObjectKey(descriptor.organizationId, descriptor.documentId)
    const target = await this.createTarget(descriptor, objectKey, nowMs)
    return { objectKey, target }
  }

  /**
   * Starts an upload with durable, tenant-scoped idempotency.
   * Replays reuse the original trusted object key and only refresh the short-lived provider target.
   */
  async initiatePersisted(
    descriptor: DocumentUploadDescriptor,
    idempotencyKey: string,
    nowMs = Date.now(),
  ): Promise<PersistedDocumentUpload> {
    assertUploadDescriptor(descriptor)
    const attempts = this.requireAttempts()
    const normalizedKey = idempotencyKey.trim()
    if (!normalizedKey) throw new Error('An idempotency key is required for document upload initiation.')

    const existing = await attempts.getByIdempotencyKey(descriptor.organizationId, normalizedKey)
    if (existing) {
      if (existing.documentId !== descriptor.documentId) {
        throw new Error('The idempotency key is already bound to a different document upload request.')
      }
      if (existing.status !== 'initiated') {
        throw new Error(`Document upload attempt is already ${existing.status}.`)
      }
      const target = await this.createTarget(descriptor, existing.objectKey, nowMs)
      return { attempt: existing, objectKey: existing.objectKey, target, replayed: true }
    }

    const objectKey = createDocumentObjectKey(descriptor.organizationId, descriptor.documentId)
    const attempt = await attempts.createOrGet(descriptor.organizationId, {
      documentId: descriptor.documentId,
      idempotencyKey: normalizedKey,
      objectKey,
    })

    // A concurrent request may have won the idempotency race. Always trust the persisted key.
    const target = await this.createTarget(descriptor, attempt.objectKey, nowMs)
    return { attempt, objectKey: attempt.objectKey, target, replayed: attempt.objectKey !== objectKey }
  }

  /**
   * Completes a persisted attempt using server-authoritative tenant/document binding.
   * Integrity is verified against provider-observed durable metadata before the compare-and-set
   * transition. Failed integrity checks are persisted as terminal failures after cleanup is attempted.
   */
  async completePersisted(
    descriptor: DocumentUploadDescriptor,
    attemptId: string,
  ): Promise<CompletedDocumentUpload> {
    assertUploadDescriptor(descriptor)
    const attempts = this.requireAttempts()
    const attempt = await attempts.getById(descriptor.organizationId, attemptId)
    if (!attempt || attempt.documentId !== descriptor.documentId) {
      throw new Error('Document upload attempt was not found for this document.')
    }
    if (attempt.status === 'failed') {
      throw new Error('Document upload attempt has already failed.')
    }

    // A completed retry is still checked against durable provider state; database state alone
    // never proves that the object still satisfies the upload contract.
    if (attempt.status === 'completed') {
      const storedObject = await this.verifyCompletion(descriptor, attempt.objectKey)
      return { attempt, storedObject, replayed: true }
    }

    let storedObject: DocumentStoredObject
    try {
      storedObject = await this.verifyCompletion(descriptor, attempt.objectKey)
    } catch (error) {
      await attempts.fail(descriptor.organizationId, attempt.id, 'object_integrity_verification_failed')
      throw error
    }

    const completed = await attempts.complete(descriptor.organizationId, attempt.id)
    if (!completed || completed.status !== 'completed') {
      throw new Error('Document upload attempt could not be completed because its state changed concurrently.')
    }
    return { attempt: completed, storedObject, replayed: false }
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

  private async createTarget(
    descriptor: DocumentUploadDescriptor,
    objectKey: string,
    nowMs: number,
  ): Promise<DocumentUploadTarget> {
    const target = await this.storage.createUploadTarget(descriptor, objectKey)
    assertUploadTarget(descriptor, target, objectKey, nowMs)
    return target
  }

  private requireAttempts(): DocumentUploadAttemptRepository {
    if (!this.attempts) throw new Error('Document upload attempt persistence is not configured.')
    return this.attempts
  }
}
