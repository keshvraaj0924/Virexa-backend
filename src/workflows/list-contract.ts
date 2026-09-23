import type { FastifyRequest } from 'fastify'
import { workflowListQuerySchema, type WorkflowListQuery } from '../contracts/workflows.js'
import { InvalidWorkflowCursorError } from './repository.js'

export type WorkflowListQueryParseResult =
  | { success: true; data: WorkflowListQuery }
  | { success: false; fieldErrors: Record<string, string[]> }

function compactFieldErrors(fieldErrors: Record<string, string[] | undefined>): Record<string, string[]> {
  return Object.fromEntries(
    Object.entries(fieldErrors).filter((entry): entry is [string, string[]] => entry[1] !== undefined),
  )
}

/**
 * Parse the public GET /api/v1/workflows query contract in one place so the
 * HTTP adapter cannot drift from the versioned contract shared with clients.
 */
export function parseWorkflowListQuery(query: FastifyRequest['query']): WorkflowListQueryParseResult {
  const parsed = workflowListQuerySchema.safeParse(query ?? {})
  if (!parsed.success) {
    return { success: false, fieldErrors: compactFieldErrors(parsed.error.flatten().fieldErrors) }
  }
  return { success: true, data: parsed.data }
}

/**
 * Cursor syntax is intentionally opaque to callers. Repository cursor errors
 * are translated by the HTTP adapter into a stable client-safe 400 envelope;
 * no persistence details are exposed.
 */
export function isInvalidWorkflowCursor(error: unknown): error is InvalidWorkflowCursorError {
  return error instanceof InvalidWorkflowCursorError
}
