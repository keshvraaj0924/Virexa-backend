import type { FastifyRequest } from 'fastify'
import type { AuthRepository } from './repository.js'
import type { AuthSession, UserRole } from '../contracts/auth.js'

export class AuthenticationRequiredError extends Error {
  constructor() {
    super('Authentication is required.')
    this.name = 'UNAUTHENTICATED'
  }
}

export class PermissionDeniedError extends Error {
  constructor() {
    super('You do not have permission to perform this action.')
    this.name = 'FORBIDDEN'
  }
}

export type Permission = 'platform:read' | 'platform:manage' | 'organization:manage' | 'audit:read'

const ROLE_PERMISSIONS: Record<UserRole, readonly Permission[]> = {
  super_admin: ['platform:read', 'platform:manage', 'organization:manage', 'audit:read'],
  admin: ['platform:read', 'organization:manage', 'audit:read'],
  manager: ['platform:read', 'audit:read'],
  operator: ['platform:read'],
  viewer: ['platform:read'],
}

export interface AuthenticatedContext extends AuthSession {
  permissions: readonly Permission[]
}

export async function requireAuthenticated(
  request: FastifyRequest,
  repository: AuthRepository,
): Promise<AuthenticatedContext> {
  const token = request.cookies.virexa_session
  if (!token) throw new AuthenticationRequiredError()

  const session = await repository.getSession(token)
  if (!session) throw new AuthenticationRequiredError()

  return {
    ...session,
    permissions: ROLE_PERMISSIONS[session.user.role],
  }
}

export function requirePermission(
  context: AuthenticatedContext,
  permission: Permission,
): AuthenticatedContext {
  if (!context.permissions.includes(permission)) throw new PermissionDeniedError()
  return context
}
