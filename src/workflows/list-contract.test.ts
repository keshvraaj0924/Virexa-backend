import { describe, expect, it } from 'vitest'
import { InvalidWorkflowCursorError } from './repository.js'
import { isInvalidWorkflowCursor, parseWorkflowListQuery } from './list-contract.js'

describe('workflow list transport contract', () => {
  it('applies the public pagination default', () => {
    expect(parseWorkflowListQuery({})).toEqual({ success: true, data: { limit: 25 } })
  })

  it('accepts status, bounded limit and opaque cursor together', () => {
    expect(parseWorkflowListQuery({ status: 'active', limit: '50', cursor: 'opaque-token' })).toEqual({
      success: true,
      data: { status: 'active', limit: 50, cursor: 'opaque-token' },
    })
  })

  it('rejects unsupported filters and invalid bounds instead of silently ignoring them', () => {
    expect(parseWorkflowListQuery({ organizationId: crypto.randomUUID() }).success).toBe(false)
    expect(parseWorkflowListQuery({ limit: '101' }).success).toBe(false)
    expect(parseWorkflowListQuery({ status: 'deleted' }).success).toBe(false)
    expect(parseWorkflowListQuery({ cursor: '' }).success).toBe(false)
  })

  it('recognizes only the repository cursor error for safe 400 translation', () => {
    expect(isInvalidWorkflowCursor(new InvalidWorkflowCursorError())).toBe(true)
    expect(isInvalidWorkflowCursor(new Error('database unavailable'))).toBe(false)
  })
})
