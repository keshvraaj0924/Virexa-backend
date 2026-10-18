# Usage and entitlements: implemented preview slice

The Python API owns a durable usage ledger and monthly UTC organization allowance.
This is not a paid subscription. Default plan `internal_preview`, status `preview`,
1000 completed document intakes per calendar month. Existing lifetime document/byte
storage limits remain separate. No commercial prices or payment state are inferred.

## Read-only frontend contract

All responses use the existing `{data, meta}` envelope and authenticated cookie.
All endpoints are under `/api/v1/usage`:

- GET `/me`: own measurements, shared organization entitlement.
- GET `/me/history`: own ledger records, no other users' records.
- GET `/organization`: own tenant aggregate and grouped operations.
- GET `/organization/history`: own tenant history, optional `userId` UUID drilldown.

Required capabilities: `usage:read` for every recognized role; tenant scope requires
`usage:organization:read`, explicitly assigned to organization/tenant/platform admins
and auditors. Platform admin never bypasses the authenticated organization scope.
Auditor access is read-only. No usage, subscription, or metering write API exists.

All endpoints accept optional `start` and `end` timezone-aware ISO datetimes, inclusive
start/exclusive end, max 93 days, default preceding 30 days. History accepts `limit`
1–100 (default25), `cursor` max2048 chars, `operation` max64, and `state`
(`reserved`, `succeeded`, `failed`, `released`). Unknown query fields grant no authority.
History returns `{items,nextCursor,start,end}`. Follow `nextCursor` with the returned
start/end unchanged plus identical filters. Cursors are opaque scope/filter-bound
keyset positions, not authorization tokens. They remain stable under newer inserts;
concurrent settlement can still update existing rows (not a database snapshot).

Summary returns `scope`, `organizationId`, `userId` (null for organization), `start`,
`end`, `meters`, `states`, `cost`, `subscription`, `groups`, `groupsTruncated`.
Meters have `{used,reserved,available}` keyed by `document_uploads`, `ocr_pages`,
`workflow_runs`, `input_tokens`, `output_tokens`, `cached_tokens`. Only document uploads
are currently available. Unsupported meter counts are null, not fabricated zero.
States count ledger operations; they are not a complete HTTP attempt/error log.
Cost is `{amount:null,currency:null,pricingVersion:null,available:false}`.
Subscription includes plan/status, `billingAvailable:false`, `scope:"organization"`,
`periodStart`, `periodEnd`, `limits.document_uploads:{limit,usedAndReserved,remaining}`.
Other limits are null. Own users may see shared allowance remaining but no identities,
operations or measurements belonging to another user. Group rows contain
`userId,operation,meter,state,events,quantity`, capped100 with explicit truncation;
filtered paginated history supplies drilldown. Do not add quantities across meters.

History items: `id,userId,operation,meter,quantity,state,errorCode,amount,currency,
pricingVersion,createdAt,updatedAt`. Known monetary amounts use decimal strings;
pricing fields stay null unless a trusted future adapter supplies all pricing data.

## Durable accounting and safety

The organization row serializes creation of periods, quota reservations and settlement
on PostgreSQL. Successful and reserved quantities consume quota. Released/failed rows
remain visible but do not consume it. Terminal settlement is idempotent for identical
state/error; transitions to a different terminal state are rejected. Keys are unique
per tenant and incompatible reuse fails. User/period composite foreign keys prevent
cross-tenant ledger associations. No client-supplied token counts are trusted.

Document completion reserves and succeeds one unit in the same transaction as the
immutable intake receipt; its document lock and server-generated key make replays
exactly once. Attribution is to the authenticated user completing intake. A quota
failure rolls back completion and may be retried after allowance becomes available.
Failed/incomplete verification does not consume allowance. The ledger does not yet
persist failed HTTP upload attempts; it must not be advertised as all application
errors. Existing pre-migration receipts are not backfilled or falsely attributed.

No long-lived reservations are created by this integration. Future async providers
must implement expiration/reconciliation before using long-lived reservations; current
internal release/failure primitives require explicit trusted settlement. Nonupload
meter reservation fails closed until actual provider entitlements are configured.
`ProviderTokens` is an internal validated response interface only; cached tokens are
a subset of input. No provider response adapter, model pricing, checkout, webhooks,
renewals, paid plan changes, seat management, personal paid subscriptions, or cost
forecasting is connected. Document extraction/OCR and workflow execution are not
metered or enabled by this change.

## Validation

`python -m pytest -q` and Ruff locally. CI uses PostgreSQL16, upgrades to exact head,
runs Alembic schema drift check and PostgreSQL usage/intake/auth/session/policy tests.
The quota contention test requires PostgreSQL row locking and skips SQLite.
Apply migration0005 only to the isolated Python schema, never the legacy Fastify DB.
