import type { Pool, PoolClient } from "pg";
import { randomUUID } from "node:crypto";
import type {
  DocumentExtraction,
  DocumentExtractionListQuery,
  ExtractionField,
  ExtractionStatus,
  ReviewDocumentExtractionRequest,
} from "../contracts/extractions.js";

export interface CreateExtractionInput {
  organizationId: string;
  documentId: string;
  schemaVersion: string;
  idempotencyKey: string;
}

export interface ListExtractionsInput extends DocumentExtractionListQuery {
  organizationId: string;
  documentId: string;
}

export interface ReviewExtractionInput extends ReviewDocumentExtractionRequest {
  organizationId: string;
  documentId: string;
  extractionId: string;
}

interface ExtractionRow {
  id: string;
  document_id: string;
  status: ExtractionStatus;
  schema_version: string;
  fields: ExtractionField[];
  failure_code: string | null;
  created_at: Date;
  updated_at: Date;
  completed_at: Date | null;
}

function mapExtraction(row: ExtractionRow): DocumentExtraction {
  return {
    id: row.id,
    documentId: row.document_id,
    status: row.status,
    schemaVersion: row.schema_version,
    fields: row.fields,
    failureCode: row.failure_code,
    createdAt: row.created_at.toISOString(),
    updatedAt: row.updated_at.toISOString(),
    completedAt: row.completed_at?.toISOString() ?? null,
  };
}

function encodeCursor(createdAt: Date, id: string): string {
  return Buffer.from(`${createdAt.toISOString()}|${id}`, "utf8").toString("base64url");
}

function decodeCursor(cursor: string): { createdAt: string; id: string } {
  const decoded = Buffer.from(cursor, "base64url").toString("utf8");
  const separator = decoded.lastIndexOf("|");
  if (separator <= 0) throw new Error("INVALID_CURSOR");
  const createdAt = decoded.slice(0, separator);
  const id = decoded.slice(separator + 1);
  if (Number.isNaN(Date.parse(createdAt)) || !id) throw new Error("INVALID_CURSOR");
  return { createdAt, id };
}

export class DocumentExtractionRepository {
  constructor(private readonly pool: Pool) {}

  async createOrReplay(input: CreateExtractionInput): Promise<DocumentExtraction> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const existing = await this.findByIdempotency(client, input);
      if (existing) {
        await client.query("COMMIT");
        return existing;
      }

      const result = await client.query<ExtractionRow>(
        `INSERT INTO document_extractions
          (id, organization_id, document_id, status, schema_version, idempotency_key)
         SELECT $1, $2, d.id, 'queued', $4, $5
         FROM documents d
         WHERE d.organization_id = $2 AND d.id = $3
         RETURNING id, document_id, status, schema_version, fields, failure_code,
                   created_at, updated_at, completed_at`,
        [randomUUID(), input.organizationId, input.documentId, input.schemaVersion, input.idempotencyKey],
      );
      if (!result.rows[0]) throw new Error("DOCUMENT_NOT_FOUND");
      await client.query("COMMIT");
      return mapExtraction(result.rows[0]);
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  private async findByIdempotency(
    client: PoolClient,
    input: CreateExtractionInput,
  ): Promise<DocumentExtraction | null> {
    const result = await client.query<ExtractionRow>(
      `SELECT id, document_id, status, schema_version, fields, failure_code,
              created_at, updated_at, completed_at
       FROM document_extractions
       WHERE organization_id = $1 AND document_id = $2 AND idempotency_key = $3`,
      [input.organizationId, input.documentId, input.idempotencyKey],
    );
    return result.rows[0] ? mapExtraction(result.rows[0]) : null;
  }

  async review(input: ReviewExtractionInput): Promise<DocumentExtraction> {
    const result = await this.pool.query<ExtractionRow>(
      `UPDATE document_extractions
       SET fields = (
         SELECT jsonb_agg(
           CASE
             WHEN patch.value IS NULL THEN original.value
             ELSE jsonb_set(original.value, '{value}', patch.value->'value', true)
           END
           ORDER BY original.ordinality
         )
         FROM jsonb_array_elements(fields) WITH ORDINALITY AS original(value, ordinality)
         LEFT JOIN jsonb_array_elements($5::jsonb) AS patch(value)
           ON patch.value->>'key' = original.value->>'key'
       ),
       updated_at = now()
       WHERE organization_id = $1
         AND document_id = $2
         AND id = $3
         AND updated_at = $4::timestamptz
         AND NOT EXISTS (
           SELECT 1
           FROM jsonb_array_elements($5::jsonb) AS requested(value)
           WHERE NOT EXISTS (
             SELECT 1
             FROM jsonb_array_elements(fields) AS original(value)
             WHERE original.value->>'key' = requested.value->>'key'
           )
         )
       RETURNING id, document_id, status, schema_version, fields, failure_code,
                 created_at, updated_at, completed_at`,
      [
        input.organizationId,
        input.documentId,
        input.extractionId,
        input.expectedUpdatedAt,
        JSON.stringify(input.fields),
      ],
    );
    if (result.rows[0]) return mapExtraction(result.rows[0]);

    const visible = await this.pool.query<{ updated_at: Date; fields: ExtractionField[] }>(
      `SELECT updated_at, fields
       FROM document_extractions
       WHERE organization_id = $1 AND document_id = $2 AND id = $3`,
      [input.organizationId, input.documentId, input.extractionId],
    );
    if (!visible.rows[0]) throw new Error("EXTRACTION_NOT_FOUND");

    const existingKeys = new Set(visible.rows[0].fields.map((field) => field.key));
    if (input.fields.some((field) => !existingKeys.has(field.key))) {
      throw new Error("EXTRACTION_REVIEW_FIELD_NOT_FOUND");
    }
    throw new Error("EXTRACTION_REVIEW_CONFLICT");
  }

  async list(input: ListExtractionsInput): Promise<{ items: DocumentExtraction[]; nextCursor: string | null }> {
    const values: unknown[] = [input.organizationId, input.documentId];
    const predicates = ["organization_id = $1", "document_id = $2"];
    if (input.status) {
      values.push(input.status);
      predicates.push(`status = $${values.length}`);
    }
    if (input.cursor) {
      const cursor = decodeCursor(input.cursor);
      values.push(cursor.createdAt, cursor.id);
      predicates.push(`(created_at, id) < ($${values.length - 1}::timestamptz, $${values.length}::uuid)`);
    }
    values.push(input.limit + 1);
    const result = await this.pool.query<ExtractionRow>(
      `SELECT id, document_id, status, schema_version, fields, failure_code,
              created_at, updated_at, completed_at
       FROM document_extractions
       WHERE ${predicates.join(" AND ")}
       ORDER BY created_at DESC, id DESC
       LIMIT $${values.length}`,
      values,
    );
    const hasMore = result.rows.length > input.limit;
    const rows = result.rows.slice(0, input.limit);
    const last = rows.at(-1);
    return {
      items: rows.map(mapExtraction),
      nextCursor: hasMore && last ? encodeCursor(last.created_at, last.id) : null,
    };
  }
}
