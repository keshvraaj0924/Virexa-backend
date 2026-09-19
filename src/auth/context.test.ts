import { describe, expect, it } from 'vitest'
import { PermissionDeniedError, requireAnyPermission, requirePermission, type AuthenticatedContext } from './context.js'

const context: AuthenticatedContext = {
  user: {
    id: 'user-1',
    email: 'admin@example.com',
    displayName: 'Admin',
    role: 'admin',
    organizationId: 'org-1',
    organizationName: 'Example Operations',
  },
  expiresAt: new Date(Date.now() + 60_000).toISOString(),
  permissions: ['platform:read', 'organization:manage', 'audit:read', 'document:read', 'document:create', 'document:manage'],
}

describe('authorization context', () => {
  it('allows permissions granted by the server-side role policy', () => {
    expect(requirePermission(context, 'organization:manage')).toBe(context)
    expect(requirePermission(context, 'audit:read')).toBe(context)
    expect(requirePermission(context, 'document:read')).toBe(context)
    expect(requirePermission(context, 'document:create')).toBe(context)
  })

  it('rejects permissions not granted to the authenticated role', () => {
    expect(() => requirePermission(context, 'platform:manage')).toThrow(PermissionDeniedError)
  })

  it('allows an action when any one of the required permissions is granted', () => {
    expect(requireAnyPermission(context, ['workflow:create', 'audit:read'])).toBe(context)
    expect(requireAnyPermission(context, ['workflow:create', 'document:manage'])).toBe(context)
  })

  it('rejects an action when none of the alternative permissions is granted', () => {
    expect(() => requireAnyPermission(context, ['workflow:create', 'workflow:manage'])).toThrow(PermissionDeniedError)
  })
})
