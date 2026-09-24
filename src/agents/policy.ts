import type { AuthenticatedContext } from '../auth/context.js'
import type { AgentProjection, AgentStatus } from './contracts.js'

/**
 * Agent mutation policy is evaluated only after tenant-scoped repository lookup.
 * Organization managers/admins with agent:manage may mutate any agent in their
 * authenticated organization. Operators with agent:create may mutate only
 * agents they created; tenant scope itself remains server/repository enforced.
 */
export function canManageAgent(context: AuthenticatedContext, agent: AgentProjection): boolean {
  if (context.permissions.includes('agent:manage')) return true
  return context.permissions.includes('agent:create') && agent.createdByUserId === context.user.id
}

const ALLOWED_STATUS_TRANSITIONS: Record<AgentStatus, readonly AgentStatus[]> = {
  draft: ['active'],
  active: ['paused', 'archived'],
  paused: ['active', 'archived'],
  archived: [],
}

export function canTransitionAgentStatus(current: AgentStatus, next: AgentStatus): boolean {
  return current === next || ALLOWED_STATUS_TRANSITIONS[current].includes(next)
}
