import { describe, expect, it } from 'vitest'
import type { AuthenticatedContext } from '../auth/context.js'
import type { AgentProjection } from './contracts.js'
import { canManageAgent, canTransitionAgentStatus } from './policy.js'

const agent: AgentProjection = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'Invoice Agent',
  description: null,
  personaId: null,
  branchId: null,
  departmentId: null,
  instructions: 'Extract invoice fields.',
  status: 'draft',
  version: 1,
  createdByUserId: '22222222-2222-4222-8222-222222222222',
  createdAt: '2026-09-24T00:00:00.000Z',
  updatedAt: '2026-09-24T00:00:00.000Z',
}

function context(userId: string, permissions: AuthenticatedContext['permissions']): AuthenticatedContext {
  return {
    user: {
      id: userId,
      email: 'user@example.com',
      displayName: 'User',
      role: 'operator',
      organizationId: '33333333-3333-4333-8333-333333333333',
      organizationName: 'Acme',
    },
    expiresAt: '2026-09-25T00:00:00.000Z',
    permissions,
  }
}

describe('agent authorization policy', () => {
  it('allows organization agent managers to mutate tenant-scoped agents', () => {
    expect(canManageAgent(context('44444444-4444-4444-8444-444444444444', ['agent:manage']), agent)).toBe(true)
  })

  it('allows creators with agent:create to mutate their own agents', () => {
    expect(canManageAgent(context(agent.createdByUserId, ['agent:create']), agent)).toBe(true)
  })

  it('does not allow creators to mutate another users agent', () => {
    expect(canManageAgent(context('44444444-4444-4444-8444-444444444444', ['agent:create']), agent)).toBe(false)
  })

  it('keeps read-only access non-mutating', () => {
    expect(canManageAgent(context(agent.createdByUserId, ['agent:read']), agent)).toBe(false)
  })
})

describe('agent lifecycle policy', () => {
  it('allows supported lifecycle transitions and idempotent status writes', () => {
    expect(canTransitionAgentStatus('draft', 'draft')).toBe(true)
    expect(canTransitionAgentStatus('draft', 'active')).toBe(true)
    expect(canTransitionAgentStatus('active', 'paused')).toBe(true)
    expect(canTransitionAgentStatus('paused', 'active')).toBe(true)
    expect(canTransitionAgentStatus('active', 'archived')).toBe(true)
  })

  it('prevents bypassing lifecycle gates or reviving archived agents', () => {
    expect(canTransitionAgentStatus('draft', 'paused')).toBe(false)
    expect(canTransitionAgentStatus('draft', 'archived')).toBe(false)
    expect(canTransitionAgentStatus('archived', 'active')).toBe(false)
    expect(canTransitionAgentStatus('archived', 'draft')).toBe(false)
  })
})
