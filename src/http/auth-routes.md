# Authentication endpoints — v1

Base path: `/api/v1/auth`

## POST `/register`
Creates a new organization and initial user. The initial user receives the `admin` role unless an explicit provisioning policy changes this in a later enterprise onboarding flow.

**Request**
```json
{
  "displayName": "Ada Lovelace",
  "email": "ada@example.com",
  "password": "strong-password",
  "organizationName": "Example Operations"
}
```

**Response** `201 Created`
```json
{
  "data": {
    "session": {
      "user": {
        "id": "user-id",
        "email": "ada@example.com",
        "displayName": "Ada Lovelace",
        "role": "admin",
        "organizationId": "org-id",
        "organizationName": "Example Operations"
      },
      "expiresAt": "2026-09-03T09:54:00.000Z"
    }
  },
  "meta": {"requestId": "request-id", "timestamp": "2026-09-02T09:54:00.000Z"}
}
```

## POST `/login`
Authenticates a user and establishes a server-managed session. The session identifier must be delivered in a `Secure`, `HttpOnly`, `SameSite=Lax` or stricter cookie scoped to the application domain.

## GET `/session`
Returns the current authenticated session. Returns `401` when no valid session exists.

## POST `/logout`
Revokes the active session and expires the session cookie.

## Security requirements

1. Passwords must be hashed with a memory-hard password hashing algorithm; plaintext passwords are never persisted or logged.
2. Login/register endpoints must be rate limited and emit generic authentication failures to reduce account enumeration.
3. CSRF protections must be applied to state-changing browser requests when cookie authentication is used.
4. Authorization is server-side and must never rely on hidden UI controls or client-provided role claims.
5. Every request carries a correlation/request ID through logs and API error responses.
