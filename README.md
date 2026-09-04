# Virexa Backend

Enterprise API foundation for Virexa.

## API principles

- Contract-first REST API
- Versioned endpoints under `/api/v1`
- Consistent success/error envelopes
- Request correlation IDs
- Structured request completion telemetry
- RBAC enforced server-side for every protected resource
- Authentication designed around secure, HttpOnly session cookies rather than browser-managed bearer secrets
- Auditability and least-privilege access as first-class concerns

## Observability

Every request receives a correlation ID. On completion, the API emits a structured `Request completed` log containing the request ID, HTTP method, matched route template, status code, and elapsed duration in milliseconds. Request bodies, cookies, authorization headers, query strings, and response payloads are intentionally excluded from this telemetry to avoid leaking credentials or tenant data.

## Operational endpoints

### `GET /health`

Liveness probe. Returns `200` when the HTTP process is responsive. It intentionally does not require database connectivity so an orchestrator can distinguish a live process from a dependency failure.

### `GET /ready`

Readiness probe. Executes a real `SELECT 1` against the configured PostgreSQL database through the application repository pool. Returns `200` only when the database dependency is reachable; otherwise returns the standard `503 DEPENDENCY_UNAVAILABLE` error envelope. Both endpoints include the request correlation ID in the standard response metadata.

## Initial authentication contract

### `POST /api/v1/auth/register`

Request:

```json
{
  "displayName": "Ada Lovelace",
  "email": "ada@example.com",
  "password": "strong-password",
  "organizationName": "Example Operations"
}
```

### `POST /api/v1/auth/login`

Request:

```json
{
  "email": "ada@example.com",
  "password": "strong-password"
}
```

### `GET /api/v1/auth/session`

Returns the authenticated user's session and organization context.

### `POST /api/v1/auth/logout`

Invalidates the active server-side session.

All successful responses use `{ "data": ..., "meta": { "requestId": ..., "timestamp": ... } }`. Errors use `{ "error": { "code": ..., "message": ..., "requestId": ..., "fieldErrors": ... }, "meta": ... }`.
