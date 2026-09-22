import type { FastifyInstance } from 'fastify'
import { z } from 'zod'
import type { AuthRepository } from '../auth/repository.js'
import { requireAuthenticated, requirePermission } from '../auth/context.js'
import { assertTrustedOrigin } from '../auth/origin-guard.js'
import type { AuditService } from '../audit/service.js'
import { apiFailure, apiSuccess } from '../contracts/http.js'
import {
  createDocumentExtractionRequestSchema,
  documentExtractionListQuerySchema,
  extractionIdempotencyKeySchema,
  type CreateDocumentExtractionRequest,
} from '../contracts/extractions.js'
import { markSensitiveResponse } from '../http/cache-policy.js'
import type { DocumentExtractionRepository } from './repository.js'

const documentIdSchema = z.string().uuid()

export interface ExtractionRouteDependencies {
  authRepository: AuthRepository
  auditService: AuditService
  extractionRepository: DocumentExtractionRepository
}

export async function registerExtractionRoutes(
  app: FastifyInstance,
  dependencies: ExtractionRouteDependencies,
): Promise<void> {
  const { authRepository, auditService, extractionRepository } = dependencies

  app.get<{ Params: { documentId: string } }>(
    '/api/v1/documents/:documentId/extractions',
    async (request, reply) => {
      markSensitiveResponse(reply)
      const context = await requireAuthenticated(request, authRepository)
      requirePermission(context, 'document:read')

      const parsedDocumentId = documentIdSchema.safeParse(request.params.documentId)
      const parsedQuery = documentExtractionListQuerySchema.safeParse(request.query ?? {})
      if (!parsedDocumentId.success || !parsedQuery.success) {
        return reply.code(400).send(apiFailure(
          'VALIDATION_ERROR',
          'Extraction query parameters are invalid.',
          request.id,
          parsedQuery.success ? undefined : parsedQuery.error.flatten().fieldErrors,
        ))
      }

      try {
        const result = await extractionRepository.list({
          organizationId: context.user.organizationId,
          documentId: parsedDocumentId.data,
          ...parsedQuery.data,
        })
        return reply.send(apiSuccess(result, request.id))
      } catch (error) {
        if (error instanceof Error && error.message === 'INVALID_CURSOR') {
          return reply.code(400).send(apiFailure('INVALID_CURSOR', 'Extraction cursor is invalid.', request.id))
        }
        throw error
      }
    },
  )

  app.post<{ Params: { documentId: string }; Body: CreateDocumentExtractionRequest }>(
    '/api/v1/documents/:documentId/extractions',
    async (request, reply) => {
      assertTrustedOrigin(request)
      markSensitiveResponse(reply)
      const context = await requireAuthenticated(request, authRepository)
      requirePermission(context, 'document:manage')

      const parsedDocumentId = documentIdSchema.safeParse(request.params.documentId)
      const parsedBody = createDocumentExtractionRequestSchema.safeParse(request.body)
      const parsedIdempotencyKey = extractionIdempotencyKeySchema.safeParse(request.headers['idempotency-key'])
      if (!parsedDocumentId.success || !parsedBody.success || !parsedIdempotencyKey.success) {
        return reply.code(400).send(apiFailure(
          'VALIDATION_ERROR',
          'Document ID, extraction request, or Idempotency-Key header is invalid.',
          request.id,
          parsedBody.success ? undefined : parsedBody.error.flatten().fieldErrors,
        ))
      }

      try {
        const extraction = await extractionRepository.createOrReplay({
          organizationId: context.user.organizationId,
          documentId: parsedDocumentId.data,
          schemaVersion: parsedBody.data.schemaVersion,
          idempotencyKey: parsedIdempotencyKey.data,
        })
        await auditService.record({
          organizationId: context.user.organizationId,
          actorUserId: context.user.id,
          action: 'document.extraction_requested',
          resourceType: 'document_extraction',
          resourceId: extraction.id,
          requestId: request.id,
          metadata: { documentId: extraction.documentId, schemaVersion: extraction.schemaVersion },
        })
        return reply.code(202).send(apiSuccess(extraction, request.id))
      } catch (error) {
        if (error instanceof Error && error.message === 'DOCUMENT_NOT_FOUND') {
          return reply.code(404).send(apiFailure('NOT_FOUND', 'Document was not found.', request.id))
        }
        throw error
      }
    },
  )
}
