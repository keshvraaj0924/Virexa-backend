export type UserRole = 'super_admin' | 'admin' | 'manager' | 'operator' | 'viewer'

export type Permission =
  | 'platform:read'
  | 'platform:manage'
  | 'organization:manage'
  | 'audit:read'
  | 'workflow:read'
  | 'workflow:create'
  | 'workflow:manage'
  | 'document:read'
  | 'document:create'
  | 'document:manage'

export type ApiErrorCode =
  | 'VALIDATION_ERROR'
  | 'UNTRUSTED_ORIGIN'
  | 'DEPENDENCY_UNAVAILABLE'
  | 'UNAUTHENTICATED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'INTERNAL_ERROR'
  | 'EMAIL_ALREADY_REGISTERED'
  | 'INVALID_CREDENTIALS'
  | 'INVALID_STATE_TRANSITION'
  | 'WORKFLOW_CONFLICT'
  | 'IDEMPOTENCY_KEY_REUSED'
  | 'RATE_LIMITED'
  | 'INVALID_CURSOR'
  | 'DOCUMENT_CHECKSUM_CONFLICT'
  | 'DOCUMENT_EXTERNAL_REFERENCE_CONFLICT'
  | 'DOCUMENT_STORAGE_UNAVAILABLE'
  | 'DOCUMENT_UPLOAD_CONFLICT'
  | 'DOCUMENT_UPLOAD_NOT_FOUND'
  | 'EXTRACTION_REVIEW_FIELD_NOT_FOUND'
  | 'EXTRACTION_REVIEW_CONFLICT'

export interface UserSummary {
  id: string
  email: string
  displayName: string
  role: UserRole
  organizationId: string
  organizationName: string
}

export interface AuthSession {
  user: UserSummary
  expiresAt: string
}

export interface SessionSummary {
  id: string
  createdAt: string
  expiresAt: string
  current: boolean
}

export interface SessionInventory {
  sessions: SessionSummary[]
}

export interface AuthenticatedContext extends AuthSession {
  permissions: readonly Permission[]
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  displayName: string
  email: string
  password: string
  organizationName: string
}

export interface ApiMeta {
  requestId: string
  timestamp: string
}

export interface ApiSuccess<T> {
  data: T
  meta: ApiMeta
}

export interface ApiErrorBody {
  code: ApiErrorCode
  message: string
  requestId: string
  fieldErrors?: Record<string, string[]>
}

export interface ApiFailure {
  error: ApiErrorBody
  meta?: ApiMeta
}
