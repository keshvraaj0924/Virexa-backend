import assert from 'node:assert/strict'
import test from 'node:test'
import type { Pool, PoolClient } from 'pg'
import {
  IdempotencyKeyConflictError,
  PostgresDocumentUploadAttemptRepository,
} from '../src/documents/upload-attempt-repository.js'

interface RecordedQuery {
  text: string
  values?: readonly unknown[]
}

const organizationId = '11111111-1111-4111-8111-111111111111'
const otherOrganizationId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const documentId = '22222222-2222-4222-8222-222222222222'
const attemptId = '33333333-3333-4333-8333-333333333333'
const now = new Date('2026-09-20T04:00:00.000Z')

function attemptRow(overrides: Record<string, unknown> = {}) {
  return {
    id: attemptId,
    organization_id: organizationId,
    document_id: documentId,
    idempotency_key: 'upload-1',
    object_key: `${organizationId}/${documentId}/source`,
    status: 'initiated',
    failure_code: null,
    created_at: now,
    completed_at: null,
    updated_at: now,
    ...overrides,
  }
}

function poolForQueries(responses: Record<string, unknown>[][]) {
  const queries: RecordedQuery[] = []
  const pool = {
    async query(text: string, values?: readonly unknown[]) {
      queries.push({ text, values })
      return { rows: responses.shift() ?? [] }
    },
    async end() {},
  } as unknown as Pool
  return { pool, queries }
}

function transactionalPool(responses: Record<string, unknown>[][]) {
  const queries: RecordedQuery[] = []
  const client = {
    async query(text: string, values?: readonly unknown[]) {
      queries.push({ text, values })
      if (text === 'BEGIN' || text === 'COMMIT' || text === 'ROLLBACK') return { rows: [] }
      return { rows: responses.shift() ?? [] }
    },
    release() {},
  } as unknown as PoolClient
  const pool = { async connect() { return client }, async end() {} } as unknown as Pool
  return { pool, queries }
}

test('getById cannot resolve an attempt without the server organization scope', async () => {
  const { pool, queries } = poolForQueries([[]])
  const repository = new PostgresDocumentUploadAttemptRepository(pool)

  assert.equal(await repository.getById(otherOrganizationId, attemptId), null)
  assert.match(queries[0].text, /WHERE organization_id = \$1 AND id = \$2/)
  assert.deepEqual(queries[0].values, [otherOrganizationId, attemptId])
})

test('getByIdempotencyKey is tenant-scoped and normalizes the replay key', async () => {
  const { pool, queries } = poolForQueries([[]])
  const repository = new PostgresDocumentUploadAttemptRepository(pool)

  assert.equal(await repository.getByIdempotencyKey(otherOrganizationId, ' upload-1 '), null)
  assert.match(queries[0].text, /WHERE organization_id = \$1 AND idempotency_key = \$2/)
  assert.deepEqual(queries[0].values, [otherOrganizationId, 'upload-1'])
})

test('getByIdempotencyKey returns the original trusted object binding for a replay', async () => {
  const row = attemptRow()
  const { pool } = poolForQueries([[row]])
  const repository = new PostgresDocumentUploadAttemptRepository(pool)

  const result = await repository.getByIdempotencyKey(organizationId, 'upload-1')

  assert.equal(result?.documentId, documentId)
  assert.equal(result?.objectKey, row.object_key)
  assert.equal(result?.status, 'initiated')
})

test('createOrGet replays the same tenant-bound request after an idempotency collision', async () => {
  const row = attemptRow()
  const { pool, queries } = transactionalPool([[], [row]])
  const repository = new PostgresDocumentUploadAttemptRepository(pool)

  const result = await repository.createOrGet(organizationId, {
    documentId,
    idempotencyKey: ' upload-1 ',
    objectKey: row.object_key as string,
  })

  assert.equal(result.id, attemptId)
  assert.match(queries[1].text, /ON CONFLICT \(organization_id, idempotency_key\) DO NOTHING/)
  assert.match(queries[2].text, /FOR UPDATE/)
  assert.deepEqual(queries[2].values, [organizationId, 'upload-1'])
  assert.equal(queries.at(-1)?.text, 'COMMIT')
})

test('createOrGet rejects reuse of an idempotency key for a different object binding', async () => {
  const { pool, queries } = transactionalPool([[], [attemptRow()]])
  const repository = new PostgresDocumentUploadAttemptRepository(pool)

  await assert.rejects(
    () => repository.createOrGet(organizationId, {
      documentId,
      idempotencyKey: 'upload-1',
      objectKey: `${organizationId}/${documentId}/different`,
    }),
    (error: unknown) => error instanceof IdempotencyKeyConflictError,
  )
  assert.equal(queries.at(-1)?.text, 'ROLLBACK')
})

test('complete is compare-and-set and returns the existing terminal state on replay', async () => {
  const completed = attemptRow({ status: 'completed', completed_at: now })
  const { pool, queries } = poolForQueries([[], [completed]])
  const repository = new PostgresDocumentUploadAttemptRepository(pool)

  const result = await repository.complete(organizationId, attemptId)

  assert.equal(result?.status, 'completed')
  assert.match(queries[0].text, /status = 'initiated'/)
  assert.match(queries[0].text, /organization_id = \$1 AND id = \$2/)
  assert.deepEqual(queries[0].values, [organizationId, attemptId])
  assert.deepEqual(queries[1].values, [organizationId, attemptId])
})

test('fail cannot overwrite a completed attempt during a racing terminal transition', async () => {
  const completed = attemptRow({ status: 'completed', completed_at: now })
  const { pool, queries } = poolForQueries([[], [completed]])
  const repository = new PostgresDocumentUploadAttemptRepository(pool)

  const result = await repository.fail(organizationId, attemptId, 'integrity_mismatch')

  assert.equal(result?.status, 'completed')
  assert.equal(result?.failureCode, null)
  assert.match(queries[0].text, /status = 'initiated'/)
  assert.deepEqual(queries[0].values, [organizationId, attemptId, 'integrity_mismatch'])
})
