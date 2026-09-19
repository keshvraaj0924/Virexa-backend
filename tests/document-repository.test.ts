import assert from 'node:assert/strict'
import test from 'node:test'
import type { Pool } from 'pg'
import { InvalidDocumentCursorError, PostgresDocumentRepository } from '../src/documents/repository.js'

interface RecordedQuery {
  text: string
  values: readonly unknown[]
}

function fakePool(rows: Record<string, unknown>[] = []) {
  const queries: RecordedQuery[] = []
  const pool = {
    async query(text: string, values: readonly unknown[]) {
      queries.push({ text, values })
      return { rows }
    },
    async end() {},
  } as unknown as Pool

  return { pool, queries }
}

const organizationId = '11111111-1111-4111-8111-111111111111'
const documentId = '22222222-2222-4222-8222-222222222222'

const createInput = {
  source: 'upload' as const,
  originalFileName: 'invoice.pdf',
  mediaType: 'application/pdf',
  sizeBytes: 4096,
  checksumSha256: 'a'.repeat(64),
}

test('create derives tenant scope from the server argument', async () => {
  const now = new Date('2026-09-19T12:00:00.000Z')
  const { pool, queries } = fakePool([{
    id: documentId,
    organization_id: organizationId,
    branch_id: null,
    department_id: null,
    source: 'upload',
    external_reference: null,
    original_file_name: 'invoice.pdf',
    media_type: 'application/pdf',
    size_bytes: '4096',
    checksum_sha256: 'a'.repeat(64),
    status: 'received',
    failure_code: null,
    received_at: now,
    updated_at: now,
  }])

  const result = await new PostgresDocumentRepository(pool).create(organizationId, createInput)

  assert.equal(queries.length, 1)
  assert.match(queries[0].text, /INSERT INTO documents/)
  assert.equal(queries[0].values[0], organizationId)
  assert.equal(result.organizationId, organizationId)
})

test('getById requires both organization and document identity', async () => {
  const { pool, queries } = fakePool()

  const result = await new PostgresDocumentRepository(pool).getById(organizationId, documentId)

  assert.equal(result, null)
  assert.equal(queries.length, 1)
  assert.match(queries[0].text, /WHERE organization_id = \$1 AND id = \$2/)
  assert.deepEqual(queries[0].values, [organizationId, documentId])
})

test('list always scopes to organization and applies status inside the same predicate', async () => {
  const { pool, queries } = fakePool()

  await new PostgresDocumentRepository(pool).list(organizationId, { status: 'review_required', limit: 25 })

  assert.equal(queries.length, 1)
  assert.match(queries[0].text, /WHERE organization_id = \$1 AND status = \$2/)
  assert.deepEqual(queries[0].values, [organizationId, 'review_required', 26])
})

test('list rejects malformed cursors before querying the database', async () => {
  const { pool, queries } = fakePool()
  const repository = new PostgresDocumentRepository(pool)

  await assert.rejects(
    () => repository.list(organizationId, { cursor: 'not-a-document-cursor', limit: 25 }),
    (error: unknown) => error instanceof InvalidDocumentCursorError,
  )

  assert.equal(queries.length, 0)
})

test('list uses deterministic tenant-scoped keyset pagination', async () => {
  const { pool, queries } = fakePool()
  const cursor = Buffer.from(JSON.stringify({
    receivedAt: '2026-09-19T11:30:00.000Z',
    id: documentId,
  }), 'utf8').toString('base64url')

  await new PostgresDocumentRepository(pool).list(organizationId, { cursor, limit: 10 })

  assert.equal(queries.length, 1)
  assert.match(queries[0].text, /organization_id = \$1/)
  assert.match(queries[0].text, /\(received_at, id\) < \(\$2::timestamptz, \$3::uuid\)/)
  assert.match(queries[0].text, /ORDER BY received_at DESC, id DESC/)
  assert.deepEqual(queries[0].values, [organizationId, '2026-09-19T11:30:00.000Z', documentId, 11])
})
