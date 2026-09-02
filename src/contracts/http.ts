import { randomUUID } from 'node:crypto'
import type { ApiMeta, ApiErrorBody } from './auth'

export function createApiMeta(requestId = randomUUID()): ApiMeta {
  return { requestId, timestamp: new Date().toISOString() }
}

export function apiSuccess<T>(data: T, requestId?: string) {
  return { data, meta: createApiMeta(requestId) }
}

export function apiFailure(code: string, message: string, requestId: string, fieldErrors?: Record<string, string[]>) {
  const error: ApiErrorBody = { code, message, requestId, ...(fieldErrors ? { fieldErrors } : {}) }
  return { error, meta: createApiMeta(requestId) }
}
