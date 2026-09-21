import type { FastifyInstance } from 'fastify'
import { z } from 'zod'
import type { AuthRepository } from '../auth/repository.js'
import { requireAuthenticated, requirePermission } from '../auth/context.js'
import { assertTrustedOrigin } from '../auth/origin-guard.js'
import type { AuditService } from '../audit/service.js'
import { apiFailure, apiSuccess } from '../contracts/http.js'
import {
  createDocumentRequestSchema,
  documentListQuerySchema,
  documentUploadIdempotencyKeySchema,
  type CreateDocumentRequest,
  type DocumentRecord,
  type DocumentUploadAttemptResponse,
} from '../contracts/documents.js'
import { markSensitiveResponse } from '../http/cache-policy.js'
import { DuplicateDocumentError, InvalidDocumentCursorError, type DocumentRepository } from './repository.js'
import type { DocumentUploadService } from './upload-service.js'

const documentIdSchema = z.string().uuid()
const uploadAttemptIdSchema = z.string().uuid()

export interface DocumentRouteDependencies {
  authRepository: AuthRepository
  auditService: AuditService
  documentRepository: DocumentRepository
  uploadService?: DocumentUploadService
}

function uploadDescriptor(organizationId: string, document: DocumentRecord) {
  return {
    organizationId,
    documentId: document.id,
    originalFileName: document.originalFileName,
    mediaType: document.mediaType,
    sizeBytes: document.sizeBytes,
    checksumSha256: document.checksumSha256,
  }
}

function publicAttempt(attempt: {
  id: string
  documentId: string
  status: 'initiated' | 'completed' | 'failed'
  createdAt: string
  completedAt: string | null
  updatedAt: string
  failureCode: string | null
}): DocumentUploadAttemptResponse {
  return {
    id: attempt.id,
    documentId: attempt.documentId,
    status: attempt.status,
    createdAt: attempt.createdAt,
    completedAt: attempt.completedAt,
    updatedAt: attempt.updatedAt,
    failureCode: attempt.failureCode,
  }
}

export async function registerDocumentRoutes(app: FastifyInstance, dependencies: DocumentRouteDependencies): Promise<void> {
  const { authRepository, auditService, documentRepository, uploadService } = dependencies

  app.get('/api/v1/documents', async (request, reply) => {
    markSensitiveResponse(reply)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'document:read')
    const parsed = documentListQuerySchema.safeParse(request.query ?? {})
    if (!parsed.success) {
      return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Document query parameters are invalid.', request.id, parsed.error.flatten().fieldErrors))
    }
    try {
      return reply.send(apiSuccess(await documentRepository.list(context.user.organizationId, parsed.data), request.id))
    } catch (error) {
      if (error instanceof InvalidDocumentCursorError) {
        return reply.code(400).send(apiFailure('INVALID_CURSOR', error.message, request.id))
      }
      throw error
    }
  })

  app.get<{ Params: { documentId: string } }>('/api/v1/documents/:documentId', async (request, reply) => {
    markSensitiveResponse(reply)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'document:read')
    const parsedId = documentIdSchema.safeParse(request.params.documentId)
    if (!parsedId.success) {
      return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Document ID is invalid.', request.id))
    }
    const document = await documentRepository.getById(context.user.organizationId, parsedId.data)
    if (!document) return reply.code(404).send(apiFailure('NOT_FOUND', 'Document was not found.', request.id))
    return reply.send(apiSuccess(document, request.id))
  })

  app.post<{ Body: CreateDocumentRequest }>('/api/v1/documents', async (request, reply) => {
    assertTrustedOrigin(request)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'document:create')
    const parsed = createDocumentRequestSchema.safeParse(request.body)
    if (!parsed.success) {
      return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Document metadata is invalid.', request.id, parsed.error.flatten().fieldErrors))
    }
    try {
      const document = await documentRepository.create(context.user.organizationId, parsed.data)
      await auditService.record({
        organizationId: context.user.organizationId,
        actorUserId: context.user.id,
        action: 'document.received',
        resourceType: 'document',
        resourceId: document.id,
        requestId: request.id,
        metadata: { source: document.source, branchId: document.branchId, departmentId: document.departmentId },
      })
      return reply.code(201).send(apiSuccess(document, request.id))
    } catch (error) {
      if (error instanceof DuplicateDocumentError) {
        return reply.code(409).send(apiFailure(
          error.reason === 'checksum' ? 'DOCUMENT_CHECKSUM_CONFLICT' : 'DOCUMENT_EXTERNAL_REFERENCE_CONFLICT',
          error.message,
          request.id,
        ))
      }
      throw error
    }
  })

  app.post<{ Params: { documentId: string } }>('/api/v1/documents/:documentId/uploads', async (request, reply) => {
    assertTrustedOrigin(request)
    markSensitiveResponse(reply)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'document:create')
    if (!uploadService) return reply.code(503).send(apiFailure('DOCUMENT_STORAGE_UNAVAILABLE', 'Document binary storage is not configured.', request.id))

    const parsedDocumentId = documentIdSchema.safeParse(request.params.documentId)
    const parsedIdempotencyKey = documentUploadIdempotencyKeySchema.safeParse(request.headers['idempotency-key'])
    if (!parsedDocumentId.success || !parsedIdempotencyKey.success) {
      return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Document ID or Idempotency-Key header is invalid.', request.id))
    }
    const document = await documentRepository.getById(context.user.organizationId, parsedDocumentId.data)
    if (!document) return reply.code(404).send(apiFailure('NOT_FOUND', 'Document was not found.', request.id))

    try {
      const initiated = await uploadService.initiatePersisted(uploadDescriptor(context.user.organizationId, document), parsedIdempotencyKey.data)
      return reply.send(apiSuccess({
        attempt: publicAttempt(initiated.attempt),
        target: {
          uploadUrl: initiated.target.uploadUrl,
          expiresAt: initiated.target.expiresAt,
          requiredHeaders: initiated.target.requiredHeaders,
        },
        replayed: initiated.replayed,
      }, request.id))
    } catch (error) {
      if (error instanceof Error && (error.message.includes('idempotency key') || error.message.includes('already completed') || error.message.includes('already failed'))) {
        return reply.code(409).send(apiFailure('DOCUMENT_UPLOAD_CONFLICT', error.message, request.id))
      }
      throw error
    }
  })

  app.post<{ Params: { documentId: string; attemptId: string } }>('/api/v1/documents/:documentId/uploads/:attemptId/complete', async (request, reply) => {
    assertTrustedOrigin(request)
    markSensitiveResponse(reply)
    const context = await requireAuthenticated(request, authRepository)
    requirePermission(context, 'document:create')
    if (!uploadService) return reply.code(503).send(apiFailure('DOCUMENT_STORAGE_UNAVAILABLE', 'Document binary storage is not configured.', request.id))

    const parsedDocumentId = documentIdSchema.safeParse(request.params.documentId)
    const parsedAttemptId = uploadAttemptIdSchema.safeParse(request.params.attemptId)
    if (!parsedDocumentId.success || !parsedAttemptId.success) {
      return reply.code(400).send(apiFailure('VALIDATION_ERROR', 'Document ID or upload attempt ID is invalid.', request.id))
    }
    const document = await documentRepository.getById(context.user.organizationId, parsedDocumentId.data)
    if (!document) return reply.code(404).send(apiFailure('NOT_FOUND', 'Document was not found.', request.id))

    try {
      const completed = await uploadService.completePersisted(uploadDescriptor(context.user.organizationId, document), parsedAttemptId.data)
      await auditService.record({
        organizationId: context.user.organizationId,
        actorUserId: context.user.id,
        action: 'document.upload_completed',
        resourceType: 'document',
        resourceId: document.id,
        requestId: request.id,
        metadata: { uploadAttemptId: completed.attempt.id, replayed: completed.replayed },
      })
      return reply.send(apiSuccess({ attempt: publicAttempt(completed.attempt), replayed: completed.replayed }, request.id))
    } catch (error) {
      if (error instanceof Error && error.message.includes('was not found for this document')) {
        return reply.code(404).send(apiFailure('DOCUMENT_UPLOAD_NOT_FOUND', error.message, request.id))
      }
      if (error instanceof Error && (error.message.includes('already failed') || error.message.includes('state changed concurrently'))) {
        return reply.code(409).send(apiFailure('DOCUMENT_UPLOAD_CONFLICT', error.message, request.id))
      }
      throw error
    }
  })
}
