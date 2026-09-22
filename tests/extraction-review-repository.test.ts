import assert from 'node:assert/strict'
import test from 'node:test'
import type { Pool } from 'pg'
import { DocumentExtractionRepository } from '../src/extractions/repository.js'

interface RecordedQuery {
  text: string
  values: readonly unknown[]
}

const organizationId = '11111111-1111-4111-8111-111111111111'
const documentId = '33333333-3333-4333-8333-333333333333'
const extractionId = '44444444-4444-4444-8444-444444444444'
const expectedUpdatedAt = '2026-09-22T00:00:00.000Z'

function extractionRow(updatedAt = new Date('2026-09-22T00:01:00.000Z')) {
  return {
    id: extractionId,
    document_id: documentId,
    status: 'review_required',
    schema_version: 'invoice-v1',
    fields: [{ key: 'invoice_number', value: 'INV-42', confidence: 0.91, sourcePage: 1, requiresReview: true }],
    failure_code: null,
    created_at: new Date('2026-09-22T00:00:00.000Z'),
    updated_at: updatedAt,
    completed_at: null,
  }
}

function sequentialPool(rowSets: Record<string, unknown>[][]) {
  const queries: RecordedQuery[] = []
  let index = 0
  const pool = {
    async query(text: string, values: readonly unknown[]) {
      queries.push({ text, values })
      return { rows: rowSets[index++] ?? [] }
    },
    async end() {},
  } as unknown as Pool
  return { pool, queries }
}

const reviewInput = {
  organizationId,
  documentId,
  extractionId,
  expectedUpdatedAt,
  fields: [{ key: 'invoice_number', value: 'INV-42' }],
}

test('review compare-and-swap is tenant scoped and only patches field values', async () => {
  const { pool, queries } = sequentialPool([[extractionRow()]])
  const result = await new DocumentExtractionRepository(pool).review(reviewInput)

  assert.equal(queries.length, 1)
  assert.match(queries[0].text, /WHERE organization_id = \$1/)
  assert.match(queries[0].text, /document_id = \$2/)
  assert.match(queries[0].text, /id = \$3/)
  assert.match(queries[0].text, /updated_at = \$4::timestamptz/)
  assert.match(queries[0].text, /jsonb_set\(original\.value, '\{value\}'/)
  assert.match(queries[0].text, /NOT EXISTS/)
  assert.deepEqual(queries[0].values.slice(0, 4), [organizationId, documentId, extractionId, expectedUpdatedAt])
  assert.equal(queries[0].values[4], JSON.stringify(reviewInput.fields))
  assert.equal(result.fields[0].confidence, 0.91)
  assert.equal(result.fields[0].sourcePage, 1)
})

test('review returns not found when extraction is not visible in the authenticated tenant', async () => {
  const { pool, queries } = sequentialPool([[], []])
  const repository = new DocumentExtractionRepository(pool)

  await assert.rejects(
    () => repository.review(reviewInput),
    (error: unknown) => error instanceof Error && error.message === 'EXTRACTION_NOT_FOUND',
  )

  assert.equal(queries.length, 2)
  assert.deepEqual(queries[1].values, [organizationId, documentId, extractionId])
  assert.match(queries[1].text, /WHERE organization_id = \$1 AND document_id = \$2 AND id = \$3/)
})

test('review rejects keys that are not present in the authoritative extraction fields', async () => {
  const unknownFieldInput = { ...reviewInput, fields: [{ key: 'caller_injected_field', value: 'bad' }] }
  const { pool, queries } = sequentialPool([[], [{ updated_at: new Date(expectedUpdatedAt), fields: extractionRow().fields }]])
  const repository = new DocumentExtractionRepository(pool)

  await assert.rejects(
    () => repository.review(unknownFieldInput),
    (error: unknown) => error instanceof Error && error.message === 'EXTRACTION_REVIEW_FIELD_NOT_FOUND',
  )

  assert.equal(queries.length, 2)
  assert.match(queries[0].text, /NOT EXISTS/)
})

test('review reports an optimistic concurrency conflict without retrying a stale mutation', async () => {
  const { pool, queries } = sequentialPool([[], [{ updated_at: new Date('2026-09-22T00:02:00.000Z'), fields: extractionRow().fields }]])
  const repository = new DocumentExtractionRepository(pool)

  await assert.rejects(
    () => repository.review(reviewInput),
    (error: unknown) => error instanceof Error && error.message === 'EXTRACTION_REVIEW_CONFLICT',
  )

  assert.equal(queries.length, 2)
  assert.match(queries[0].text, /updated_at = \$4::timestamptz/)
})
