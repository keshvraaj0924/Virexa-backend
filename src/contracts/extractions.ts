import { z } from "zod";

export const extractionStatuses = [
  "queued",
  "processing",
  "review_required",
  "completed",
  "failed",
] as const;

export const extractionStatusSchema = z.enum(extractionStatuses);
export const extractionFieldValueSchema = z.union([
  z.string(),
  z.number(),
  z.boolean(),
  z.null(),
]);

export const extractionFieldSchema = z.object({
  key: z.string().trim().min(1).max(128),
  value: extractionFieldValueSchema,
  confidence: z.number().min(0).max(1),
  sourcePage: z.number().int().positive().nullable(),
  requiresReview: z.boolean(),
}).strict();

// Public API projection. Tenant identifiers and provider/storage internals are
// intentionally absent: organization scope is derived from the authenticated
// session and must never be caller-selectable.
export const documentExtractionSchema = z.object({
  id: z.string().uuid(),
  documentId: z.string().uuid(),
  status: extractionStatusSchema,
  schemaVersion: z.string().trim().min(1).max(64),
  fields: z.array(extractionFieldSchema),
  failureCode: z.string().max(100).nullable(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  completedAt: z.string().datetime().nullable(),
}).strict();

// POST /api/v1/documents/:documentId/extractions
// Idempotency-Key is required at the HTTP boundary. Provider/model selection is
// deliberately server-side so tenants cannot bypass configured AI policy.
export const createDocumentExtractionRequestSchema = z.object({
  schemaVersion: z.string().trim().min(1).max(64),
}).strict();

export const documentExtractionListQuerySchema = z.object({
  status: extractionStatusSchema.optional(),
  cursor: z.string().min(1).optional(),
  limit: z.coerce.number().int().min(1).max(100).default(50),
}).strict();

export const documentExtractionListResponseSchema = z.object({
  items: z.array(documentExtractionSchema),
  nextCursor: z.string().nullable(),
}).strict();

export const extractionIdempotencyKeySchema = z.string().trim().min(1).max(255);

export type ExtractionStatus = z.infer<typeof extractionStatusSchema>;
export type ExtractionField = z.infer<typeof extractionFieldSchema>;
export type DocumentExtraction = z.infer<typeof documentExtractionSchema>;
export type CreateDocumentExtractionRequest = z.infer<typeof createDocumentExtractionRequestSchema>;
export type DocumentExtractionListQuery = z.infer<typeof documentExtractionListQuerySchema>;
export type DocumentExtractionListResponse = z.infer<typeof documentExtractionListResponseSchema>;
