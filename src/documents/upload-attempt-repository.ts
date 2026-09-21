import { Pool } from 'pg'

export type DocumentUploadAttemptStatus = 'initiated' | 'completed' | 'failed'

export interface DocumentUploadAttempt {
  id: string
  organizationId: string
  documentId: string
  idempotencyKey: string
  objectKey: string
  status: DocumentUploadAttemptStatus
  failureCode: string | null
  createdAt: string
  completedAt: string | null
  updatedAt: string
}

export interface CreateDocumentUploadAttempt {
  documentId: string
  idempotencyKey: string
  objectKey: string
}

export interface DocumentUploadAttemptRepository {
  createOrGet(organizationId: string, input: CreateDocumentUploadAttempt): Promise<DocumentUploadAttempt>
  getById(organizationId: string, attemptId: string): Promise<DocumentUploadAttempt | null>
  getByIdempotencyKey(organizationId: string, idempotencyKey: string): Promise<DocumentUploadAttempt | null>
  complete(organizationId: string, attemptId: string): Promise<DocumentUploadAttempt | null>
  fail(organizationId: string, attemptId: string, failureCode: string): Promise<DocumentUploadAttempt | null>
  close?(): Promise<void>
}

export class IdempotencyKeyConflictError extends Error {
  constructor() {
    super('The idempotency key is already bound to a different document upload request.')
    this.name = 'IDEMPOTENCY_KEY_CONFLICT'
  }
}

function mapAttempt(row: any): DocumentUploadAttempt {
  return {
    id: row.id,
    organizationId: row.organization_id,
    documentId: row.document_id,
    idempotencyKey: row.idempotency_key,
    objectKey: row.object_key,
    status: row.status as DocumentUploadAttemptStatus,
    failureCode: row.failure_code ?? null,
    createdAt: row.created_at.toISOString(),
    completedAt: row.completed_at?.toISOString() ?? null,
    updatedAt: row.updated_at.toISOString(),
  }
}

const columns = `
  id, organization_id, document_id, idempotency_key, object_key, status,
  failure_code, created_at, completed_at, updated_at
`

export class PostgresDocumentUploadAttemptRepository implements DocumentUploadAttemptRepository {
  constructor(private readonly pool: Pool) {}

  async createOrGet(organizationId: string, input: CreateDocumentUploadAttempt): Promise<DocumentUploadAttempt> {
    const client = await this.pool.connect()
    try {
      await client.query('BEGIN')
      const inserted = await client.query(
        `INSERT INTO document_upload_attempts (
          organization_id, document_id, idempotency_key, object_key
        ) VALUES ($1, $2, $3, $4)
        ON CONFLICT (organization_id, idempotency_key) DO NOTHING
        RETURNING ${columns}`,
        [organizationId, input.documentId, input.idempotencyKey.trim(), input.objectKey],
      )

      if (inserted.rows[0]) {
        await client.query('COMMIT')
        return mapAttempt(inserted.rows[0])
      }

      const existing = await client.query(
        `SELECT ${columns}
         FROM document_upload_attempts
         WHERE organization_id = $1 AND idempotency_key = $2
         FOR UPDATE`,
        [organizationId, input.idempotencyKey.trim()],
      )
      const row = existing.rows[0]
      if (!row || row.document_id !== input.documentId || row.object_key !== input.objectKey) {
        throw new IdempotencyKeyConflictError()
      }

      await client.query('COMMIT')
      return mapAttempt(row)
    } catch (error) {
      await client.query('ROLLBACK')
      throw error
    } finally {
      client.release()
    }
  }

  async getById(organizationId: string, attemptId: string): Promise<DocumentUploadAttempt | null> {
    const result = await this.pool.query(
      `SELECT ${columns}
       FROM document_upload_attempts
       WHERE organization_id = $1 AND id = $2`,
      [organizationId, attemptId],
    )
    return result.rows[0] ? mapAttempt(result.rows[0]) : null
  }

  async getByIdempotencyKey(organizationId: string, idempotencyKey: string): Promise<DocumentUploadAttempt | null> {
    const result = await this.pool.query(
      `SELECT ${columns}
       FROM document_upload_attempts
       WHERE organization_id = $1 AND idempotency_key = $2`,
      [organizationId, idempotencyKey.trim()],
    )
    return result.rows[0] ? mapAttempt(result.rows[0]) : null
  }

  async complete(organizationId: string, attemptId: string): Promise<DocumentUploadAttempt | null> {
    const result = await this.pool.query(
      `UPDATE document_upload_attempts
       SET status = 'completed', completed_at = now(), failure_code = NULL, updated_at = now()
       WHERE organization_id = $1 AND id = $2 AND status = 'initiated'
       RETURNING ${columns}`,
      [organizationId, attemptId],
    )
    if (result.rows[0]) return mapAttempt(result.rows[0])
    return this.getById(organizationId, attemptId)
  }

  async fail(organizationId: string, attemptId: string, failureCode: string): Promise<DocumentUploadAttempt | null> {
    const result = await this.pool.query(
      `UPDATE document_upload_attempts
       SET status = 'failed', completed_at = NULL, failure_code = $3, updated_at = now()
       WHERE organization_id = $1 AND id = $2 AND status = 'initiated'
       RETURNING ${columns}`,
      [organizationId, attemptId, failureCode],
    )
    if (result.rows[0]) return mapAttempt(result.rows[0])
    return this.getById(organizationId, attemptId)
  }

  async close(): Promise<void> {
    await this.pool.end()
  }
}
