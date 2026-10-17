# Python session controls (opt-in candidate)

Stacked on the tenant document quarantine candidate; no deployment or legacy
Fastify replacement. Session tokens/digests are never inventory fields.

## Browser contract

All routes use `/api/v1`, existing private/no-store camelCase envelopes and live
cookie-backed database authority. Read requires a valid session; mutations also
require an exact allowed Origin. No client tenant/user/role headers grant scope.

- `GET /auth/sessions?limit=50&cursor=<UUID>` returns `data: ActiveSession[]`.
  Each item has only `id`, `createdAt`, `expiresAt`, and `current`.
  `meta.pagination.nextCursor` is a UUID or null. Limits are 1–100; stable UUID
  ascending keyset order, not creation-time order. Follow cursors until null to
  see all active sessions. The current session may be on a later page. Cursors
  are positions, not credentials; every page reapplies user and tenant predicates.
  Listing is live, not a snapshot: new sessions before a cursor appear on refresh.
- `POST /auth/sessions/revoke-others` returns `data: {revokedCount: number}`.
  All active sessions for this owner except the caller are revoked, regardless
  of pagination. Repeating returns zero. Current cookie is not modified.
- `DELETE /auth/sessions/{UUID}` returns `data: {success: true}`. Revoking the
  current session clears its cookie with matching security attributes. An ID
  belonging to another user/tenant, absent, expired, or already revoked returns
  identical 404. A replayed revoked caller cookie returns 401, including when
  retrying self-revocation. Malformed UUIDs/limits return 400.
- Existing `POST /auth/logout` remains idempotent and clears even stale cookies.

The frontend's existing array shape remains compatible, but its Python proxy must
explicitly allow these routes and its security page must consume pagination.
Do not imply every session is shown after only one page.

## Concurrency and security

PostgreSQL owner-row locking serializes session issuance, logout, targeted
revocation and revoke-others. Mutations re-resolve the caller after acquiring the
lock, so a concurrent winner's revoked cookie cannot authorize a second mutation.
No process-local mutex is used for authority. Revocation commits before success;
subsequent authentication requests reject the cookie immediately. Work already
authenticated before revocation is not retroactively canceled. Independent login
that serializes after a revocation issues a new session; revoke-others is not an
account lock or credential rotation. User and organization predicates occur in
SQL, including when an administrator requests another user's session ID.

## Verification and limits

Run `python -m pytest -q` and Ruff locally. `.github/workflows/python-foundation.yml`
pins the exact PR head and runs this suite on PostgreSQL 16, including mutual
revocation, simultaneous repeated revocations, and revoked-cookie replay. SQLite
skips the mutual-revocation row-lock test and is not locking evidence. Synthetic
accounts only. No schema migration is needed.

Existing production gates are unchanged: process-local auth rate limits need a
shared production limiter; private document storage is opt-in, scan remains
pending and no extraction/clean receipt is implemented. This change adds session
management, not production certification.
