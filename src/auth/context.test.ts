import { describe, expect, it } from 'vitest'
import { PermissionDeniedError, requirePermission, type AuthenticatedContext } from './context.js'

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
  permissions: ['platform:read', 'organization:manage', 'audit:read'],
}

describe('authorization context', () => {
  it('allows permissions granted by the server-side role policy', () => {
    expect(requirePermission(context, 'organization:manage')).toBe(context)
    expect(requirePermission(context, 'audit:read')).toBe(context)
  })

  it('rejects permissions not granted to the authenticated role', () => {
    expect(() => requirePermission(context, 'platform:manage')).toThrow(PermissionDeniedError)
  })
})
