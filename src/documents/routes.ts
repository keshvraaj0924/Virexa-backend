import type { FastifyInstance } from 'fastify'
import { z } from 'zod'
import type { AuthRepository } from '../auth/repository.js'
import { requireAuthenticated, requirePermission } from '../auth/context.js'
import { assertTrustedOrigin } from '../auth/origin-guard.js'
import type { AuditService } from '../audit/service.js'
import { apiFailure, apiSuccess } from '../contracts/http.js'
import { createDocumentRequestSchema, documentListQuerySchema, type CreateDocumentRequest } from '../contracts/documents.js'
import { markSensitiveResponse } from '../http/cache-policy.js'
import { DuplicateDocumentError, InvalidDocumentCursorError, type DocumentRepository } from './repository.js'

const documentIdSchema = z.string().uuid()

export interface DocumentRouteDependencies {
  authRepository: AuthRepository
  auditService: AuditService
  documentRepository: DocumentRepository
}

export async function registerDocumentRoutes(app: FastifyInstance, dependencies: DocumentRouteDependencies): Promise<void> {
  const { authRepository, auditService, documentRepository } = dependencies

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
}
