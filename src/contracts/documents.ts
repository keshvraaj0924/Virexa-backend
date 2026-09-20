import { z } from "zod";

export const documentStatuses = [
  "received",
  "processing",
  "review_required",
  "completed",
  "failed",
] as const;

export const documentStatusSchema = z.enum(documentStatuses);
export const documentSourceSchema = z.enum(["upload", "email", "api", "integration"]);

export const documentSchema = z.object({
  id: z.string().uuid(),
  organizationId: z.string().uuid(),
  branchId: z.string().uuid().nullable(),
  departmentId: z.string().uuid().nullable(),
  source: documentSourceSchema,
  externalReference: z.string().max(255).nullable(),
  originalFileName: z.string().min(1).max(512),
  mediaType: z.string().min(1).max(255),
  sizeBytes: z.number().int().positive(),
  checksumSha256: z.string().regex(/^[a-f0-9]{64}$/),
  status: documentStatusSchema,
  failureCode: z.string().max(100).nullable(),
  receivedAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});

// Tenant scope is server-authoritative. Callers can never select organizationId.
export const createDocumentRequestSchema = z.object({
  branchId: z.string().uuid().optional(),
  departmentId: z.string().uuid().optional(),
  source: documentSourceSchema.default("upload"),
  externalReference: z.string().trim().min(1).max(255).optional(),
  originalFileName: z.string().trim().min(1).max(512),
  mediaType: z.string().trim().min(1).max(255),
  sizeBytes: z.number().int().positive(),
  checksumSha256: z.string().regex(/^[a-f0-9]{64}$/),
}).strict();

export const documentListQuerySchema = z.object({
  status: documentStatusSchema.optional(),
  cursor: z.string().min(1).optional(),
  limit: z.coerce.number().int().min(1).max(100).default(50),
}).strict();

export const documentListResponseSchema = z.object({
  items: z.array(documentSchema),
  nextCursor: z.string().nullable(),
});

// Upload lifecycle contract for /api/v1. Idempotency is supplied in the
// Idempotency-Key header and tenant scope is always derived from the session.
export const documentUploadIdempotencyKeySchema = z.string().trim().min(1).max(255);
export const documentUploadAttemptStatusSchema = z.enum(["initiated", "completed", "failed"]);

export const documentUploadAttemptSchema = z.object({
  id: z.string().uuid(),
  documentId: z.string().uuid(),
  status: documentUploadAttemptStatusSchema,
  createdAt: z.string().datetime(),
  completedAt: z.string().datetime().nullable(),
  failedAt: z.string().datetime().nullable(),
  failureCode: z.string().max(100).nullable(),
});

export const documentUploadTargetSchema = z.object({
  uploadUrl: z.string().url().refine((value) => value.startsWith("https://"), "Upload URL must use HTTPS"),
  expiresAt: z.string().datetime(),
  requiredHeaders: z.record(z.string(), z.string()),
});

export const initiateDocumentUploadResponseSchema = z.object({
  attempt: documentUploadAttemptSchema,
  target: documentUploadTargetSchema,
  replayed: z.boolean(),
});

export const completeDocumentUploadResponseSchema = z.object({
  attempt: documentUploadAttemptSchema,
  replayed: z.boolean(),
});

export type DocumentStatus = z.infer<typeof documentStatusSchema>;
export type DocumentSource = z.infer<typeof documentSourceSchema>;
export type DocumentRecord = z.infer<typeof documentSchema>;
export type CreateDocumentRequest = z.infer<typeof createDocumentRequestSchema>;
export type DocumentListQuery = z.infer<typeof documentListQuerySchema>;
export type DocumentListResponse = z.infer<typeof documentListResponseSchema>;
export type DocumentUploadAttemptResponse = z.infer<typeof documentUploadAttemptSchema>;
export type InitiateDocumentUploadResponse = z.infer<typeof initiateDocumentUploadResponseSchema>;
export type CompleteDocumentUploadResponse = z.infer<typeof completeDocumentUploadResponseSchema>;
