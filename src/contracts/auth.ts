export type UserRole = 'super_admin' | 'admin' | 'manager' | 'operator' | 'viewer'

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
  code: string
  message: string
  requestId: string
  fieldErrors?: Record<string, string[]>
}

export interface ApiFailure {
  error: ApiErrorBody
  meta?: ApiMeta
}
