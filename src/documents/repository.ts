import { Pool } from 'pg'
import type {
  CreateDocumentRequest,
  DocumentListQuery,
  DocumentRecord,
  DocumentStatus,
} from '../contracts/documents.js'

export interface DocumentListResult {
  items: DocumentRecord[]
  nextCursor: string | null
}

export interface DocumentRepository {
  create(organizationId: string, input: CreateDocumentRequest): Promise<DocumentRecord>
  list(organizationId: string, query: DocumentListQuery): Promise<DocumentListResult>
  getById(organizationId: string, documentId: string): Promise<DocumentRecord | null>
  close?(): Promise<void>
}

type DocumentCursor = {
  receivedAt: string
  id: string
}

const columns = `
  id, organization_id, branch_id, department_id, source, external_reference,
  original_file_name, media_type, size_bytes, checksum_sha256, status,
  failure_code, received_at, updated_at
`

function mapDocument(row: any): DocumentRecord {
  return {
    id: row.id,
    organizationId: row.organization_id,
    branchId: row.branch_id ?? null,
    departmentId: row.department_id ?? null,
    source: row.source,
    externalReference: row.external_reference ?? null,
    originalFileName: row.original_file_name,
    mediaType: row.media_type,
    sizeBytes: Number(row.size_bytes),
    checksumSha256: row.checksum_sha256,
    status: row.status as DocumentStatus,
    failureCode: row.failure_code ?? null,
    receivedAt: row.received_at.toISOString(),
    updatedAt: row.updated_at.toISOString(),
  }
}

function encodeCursor(document: DocumentRecord): string {
  return Buffer.from(JSON.stringify({ receivedAt: document.receivedAt, id: document.id } satisfies DocumentCursor), 'utf8').toString('base64url')
}

function decodeCursor(cursor: string): DocumentCursor {
  try {
    const parsed = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8')) as Partial<DocumentCursor>
    if (typeof parsed.receivedAt !== 'string' || typeof parsed.id !== 'string') throw new Error('invalid cursor')
    if (Number.isNaN(Date.parse(parsed.receivedAt))) throw new Error('invalid cursor timestamp')
    return { receivedAt: parsed.receivedAt, id: parsed.id }
  } catch {
    throw new InvalidDocumentCursorError()
  }
}

export class InvalidDocumentCursorError extends Error {
  constructor() {
    super('The document cursor is invalid.')
    this.name = 'INVALID_DOCUMENT_CURSOR'
  }
}

export class PostgresDocumentRepository implements DocumentRepository {
  constructor(private readonly pool: Pool) {}

  async create(organizationId: string, input: CreateDocumentRequest): Promise<DocumentRecord> {
    const result = await this.pool.query(
      `INSERT INTO documents (
        organization_id, branch_id, department_id, source, external_reference,
        original_file_name, media_type, size_bytes, checksum_sha256
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING ${columns}`,
      [
        organizationId,
        input.branchId ?? null,
        input.departmentId ?? null,
        input.source,
        input.externalReference ?? null,
        input.originalFileName.trim(),
        input.mediaType.trim(),
        input.sizeBytes,
        input.checksumSha256,
      ],
    )
    return mapDocument(result.rows[0])
  }

  async list(organizationId: string, query: DocumentListQuery): Promise<DocumentListResult> {
    const values: unknown[] = [organizationId]
    const predicates = ['organization_id = $1']

    if (query.status) {
      values.push(query.status)
      predicates.push(`status = $${values.length}`)
    }

    if (query.cursor) {
      const cursor = decodeCursor(query.cursor)
      values.push(cursor.receivedAt, cursor.id)
      predicates.push(`(received_at, id) < ($${values.length - 1}::timestamptz, $${values.length}::uuid)`)
    }

    values.push(query.limit + 1)
    const result = await this.pool.query(
      `SELECT ${columns}
       FROM documents
       WHERE ${predicates.join(' AND ')}
       ORDER BY received_at DESC, id DESC
       LIMIT $${values.length}`,
      values,
    )

    const documents = result.rows.map(mapDocument)
    const hasMore = documents.length > query.limit
    const items = hasMore ? documents.slice(0, query.limit) : documents
    return {
      items,
      nextCursor: hasMore && items.length > 0 ? encodeCursor(items[items.length - 1]) : null,
    }
  }

  async getById(organizationId: string, documentId: string): Promise<DocumentRecord | null> {
    const result = await this.pool.query(
      `SELECT ${columns} FROM documents WHERE organization_id = $1 AND id = $2`,
      [organizationId, documentId],
    )
    return result.rows[0] ? mapDocument(result.rows[0]) : null
  }

  async close(): Promise<void> {
    await this.pool.end()
  }
}
