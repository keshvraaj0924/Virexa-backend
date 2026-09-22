import assert from 'node:assert/strict'
import test from 'node:test'
import type { Pool } from 'pg'
import { DocumentExtractionRepository } from '../src/extractions/repository.js'

const organizationId = '11111111-1111-4111-8111-111111111111'
const documentId = '33333333-3333-4333-8333-333333333333'
const extractionId = '44444444-4444-4444-8444-444444444444'
const expectedUpdatedAt = '2026-09-22T00:00:00.000Z'

function completedRow() {
  return {
    id: extractionId,
    document_id: documentId,
    status: 'completed',
    schema_version: 'invoice-v1',
    fields: [{ key: 'invoice_number', value: 'INV-42', confidence: 0.91, sourcePage: 1, requiresReview: true }],
    failure_code: null,
    created_at: new Date('2026-09-22T00:00:00.000Z'),
    updated_at: new Date('2026-09-22T00:01:00.000Z'),
    completed_at: new Date('2026-09-22T00:01:00.000Z'),
  }
}

function sequentialPool(rowSets: Record<string, unknown>[][]) {
  const queries: Array<{ text: string; values: readonly unknown[] }> = []
  let index = 0
  const pool = {
    async query(text: string, values: readonly unknown[]) {
      queries.push({ text, values })
      return { rows: rowSets[index++] ?? [] }
    },
  } as unknown as Pool
  return { pool, queries }
}

const input = { organizationId, documentId, extractionId, expectedUpdatedAt }

test('completion is tenant scoped, review-state gated, and compare-and-swap protected', async () => {
  const { pool, queries } = sequentialPool([[completedRow()]])
  const result = await new DocumentExtractionRepository(pool).complete(input)
  assert.equal(result.status, 'completed')
  assert.equal(result.completedAt, '2026-09-22T00:01:00.000Z')
  assert.match(queries[0].text, /organization_id = \$1/)
  assert.match(queries[0].text, /document_id = \$2/)
  assert.match(queries[0].text, /id = \$3/)
  assert.match(queries[0].text, /updated_at = \$4::timestamptz/)
  assert.match(queries[0].text, /status = 'review_required'/)
  assert.deepEqual(queries[0].values, [organizationId, documentId, extractionId, expectedUpdatedAt])
})

test('completion does not reveal cross-tenant or missing extraction', async () => {
  const { pool } = sequentialPool([[], []])
  await assert.rejects(() => new DocumentExtractionRepository(pool).complete(input), (error: unknown) => error instanceof Error && error.message === 'EXTRACTION_NOT_FOUND')
})

test('completion rejects authoritative lifecycle states other than review_required', async () => {
  const { pool } = sequentialPool([[], [{ status: 'processing', updated_at: new Date(expectedUpdatedAt) }]])
  await assert.rejects(() => new DocumentExtractionRepository(pool).complete(input), (error: unknown) => error instanceof Error && error.message === 'EXTRACTION_COMPLETION_INVALID_STATE')
})

test('completion reports optimistic concurrency conflict for stale review state', async () => {
  const { pool } = sequentialPool([[], [{ status: 'review_required', updated_at: new Date('2026-09-22T00:02:00.000Z') }]])
  await assert.rejects(() => new DocumentExtractionRepository(pool).complete(input), (error: unknown) => error instanceof Error && error.message === 'EXTRACTION_COMPLETION_CONFLICT')
})
