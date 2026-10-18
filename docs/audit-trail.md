# Python tenant audit trail

The Python candidate now writes forward-only, durable `audit_events` for actual supported operations. Nothing is backfilled. Coverage starts when this version is enabled against an Alembic-migrated candidate database; an empty list does not mean no activity ever occurred. No extraction/model jobs, email delivery, anonymous authentication failures, or external system completeness is claimed.

## Schema and migration boundary

`0007_audit_events` follows `0006_verified_signup`. It maps the existing Node table's columns and public event shape, using metadata only for the fixed outcome. Python and Node have separate database/migration chains; this is not a migration of an existing Node-managed database. Never apply both migration chains to the same database. No incompatible duplicate audit table or assumed account/job fields are introduced. The tenant/time index adds the unique ID tie-breaker for pagination.

## Supported events

- `auth.signup.completed`: verified HTTP signup (and test-only legacy registration)
- `auth.login`: actual successful session creation
- `auth.logout`: a session's first revocation through logout
- `auth.session.revoked`: one event per session actually revoked, including revoke-others
- `document.created`: metadata persisted
- `document.intake.completed`: first verified quarantine completion, atomically with usage settlement
- `document.scan.clean`, `document.scan.rejected`, `document.scan.error`: internal worker verdict/retry outcomes, attributed to the initiating upload actor; no raw engine signature or content
- `document.intake.failed`: authenticated document mutation returning a handled application error, excluding extraction requests
- `access.denied`: authenticated API 403/404 errors after a valid database-backed subject was resolved

Successful mutations and their events commit or roll back together. Replayed upload completions and repeated revocation do not duplicate success events. A new failed HTTP attempt is a distinct event. Error events use their own transaction after business rollback and are best effort if database recording is unavailable. Validation errors, pre-authentication/rate/origin rejections, cancellations, unexpected exceptions and middleware-level rejects are not exhaustively captured. Direct service calls without a request correlation ID are not HTTP event coverage.

Only server-generated request IDs, authenticated actor/tenant UUIDs, fixed action/resource type/outcome, server timestamp and optional opaque resource UUID are written. No passwords, tokens, verification codes, request bodies, query strings, filenames, PDF contents or provider secrets. Denied cross-tenant and nonexistent references both record a generic null target in the caller's own tenant; resource existence is never queried across tenant boundaries for audit enrichment. Authenticated failure events are operation outcomes, not claims of malicious behavior.

## Read contract and policy

`GET /api/v1/audit/events` preserves `data.events` and Node event fields: `id`, `organizationId`, `actorUserId`, `action`, `resourceType`, `resourceId`, `requestId`, `metadata`, `createdAt`. Metadata is serialized from the fixed action allowlist, never passed through from arbitrary database JSON. Adds `data.nextCursor` and `data.coverage` (mode/version/actions/notice). Only this Python event allowlist is visible.

Query: `limit` 1–100 (default 50), `cursor` up to 2048 characters, supported `action`, `outcome` (`succeeded`, `failed`, `denied`). Unknown selectors, including organization/actor IDs, are rejected. Descending timestamp/UUID keysets are scope/filter-bound; new activity is obtained by refreshing from the first page. This is not a snapshot export. No unbounded count or export endpoint.

The central live-role policy exposes `audit:read` for organization/tenant/platform administrators and auditors. All are constrained by the same authenticated tenant predicate, with no platform cross-organization bypass. Personal signup owners already have the organization-admin role for their own workspace. Managers/operators/viewers cannot read audit events. This is deliberately narrower than the legacy Node manager permission catalogue. Responses remain private/no-store. No POST/PATCH/PUT/DELETE audit endpoints exist.

This is application-level server-only append behavior, not cryptographic tamper proofing or protection from database owners/administrators. There is no retention/deletion UI, immutable storage, or legal compliance guarantee in this milestone.
