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
- Authenticated data responses explicitly disable browser/intermediary caching
- Authentication endpoints enforce abuse-resistant request limits
- Auditability and least-privilege access as first-class concerns

## Observability

Every request receives a correlation ID. On completion, the API emits a structured `Request completed` log containing the request ID, HTTP method, matched route template, status code, and elapsed duration in milliseconds. Request bodies, cookies, authorization headers, query strings, and response payloads are intentionally excluded from this telemetry to avoid leaking credentials or tenant data.

## Authenticated response caching

Responses that expose authenticated identity, audit, or workflow data are marked `Cache-Control: private, no-store, max-age=0`, with `Pragma: no-cache` and `Expires: 0`. This prevents browser and intermediary caches from retaining tenant-scoped operational data or session context. Public liveness/readiness responses are not subject to this application-level sensitive-data policy.

## Authentication abuse protection

`POST /api/v1/auth/register` and `POST /api/v1/auth/login` are limited to 10 requests per client IP within a 5-minute window. The API returns HTTP `429` with the versioned `RATE_LIMITED` error code and a `Retry-After` response header when the limit is exceeded.

The limiter uses the process-local store provided by `@fastify/rate-limit`. This is intentionally a first-layer control for the current single-process deployment shape. A multi-replica deployment must provide a shared rate-limit store (for example Redis) before relying on the limit as a fleet-wide quota.

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

Returns the authenticated user's session and organization context. The response is explicitly non-cacheable.

### `POST /api/v1/auth/logout`

Invalidates the active server-side session.

All successful responses use `{ "data": ..., "meta": { "requestId": ..., "timestamp": ... } }`. Errors use `{ "error": { "code": ..., "message": ..., "requestId": ..., "fieldErrors": ... }, "meta": ... }`.
