# Python candidate access policies

This milestone is stacked on the Python candidate. It does not activate the
candidate, extraction, workflows, agents, ERP, or tenant-wide session administration.
The Node role model and `/me` legacy permissions are compatibility data, not
Python authorization. The old `Principal.tenant_id` helper is not used by HTTP
routes: in the current Python database, **organization is the tenant boundary**.
There is no document-owner field and no fabricated owner restriction.

## Capability discovery

`GET /api/v1/me/permissions`, using the existing HttpOnly session cookie, returns:

```json
{"data":{"capabilities":["document:create","document:read","document:upload","organization:read","session:read","session:revoke"]},"meta":{"requestId":"...","timestamp":"..."}}
```

Only capability IDs for implemented operations are returned. This is a snapshot,
not a bearer grant or resource authorization. Responses are private/no-store.
Clients must fail closed on lookup failure and refresh on account/session change
or a 403. The server re-reads the user role and session from the database on every
request. Request role/organization headers and client permission arrays are never
an authority source. Unknown roles and actions fail closed.

The existing `/me` contract remains compatible; auditor now has an empty legacy
permission list instead of being rejected there. The new endpoint gives auditors
their implemented session-only capabilities. No unimplemented audit view is
advertised. Python-to-wire aliases remain platform_admin → super_admin and
both tenant_admin and organization_admin → admin.

## Preserved role matrix

| Persisted role | Documents read | Create/upload | Organization read | Own sessions read/revoke |
| --- | --- | --- | --- | --- |
| platform_admin | Yes | Yes | Yes | Yes |
| tenant_admin | Yes | Yes | Yes | Yes |
| organization_admin | Yes | Yes | Yes | Yes |
| manager | Yes | Yes | Yes | Yes |
| operator | Yes | Yes | No | Yes |
| viewer | Yes | No | No | Yes |
| auditor | No | No | No | Yes |

No role gets cross-organization access. Operator retains upload intake, but the
unavailable extraction-management route remains forbidden to operators. Other
writers still need a clean scan to reach its explicit 503; extraction is never
included in effective capabilities.

## Resource attributes and decision order

`app/core/policy.py` is the central typed policy. Authentication precedes role
checks; collection queries and resource lookups include trusted organization
predicates. Foreign and nonexistent resources share 404s, never hidden metadata.

- Document metadata reads are organization-scoped regardless of state.
- New upload attempts, unfinished-attempt replay/renewal, content writes, and
  first completion require document status `received` plus scan `awaiting_upload`.
- Content writes require an initiated, unexpired attempt. First completion now
  requires this too, plus verified bytes. Expired attempts can be renewed with
  their original idempotency key while the document is still writable.
- Completed-attempt replay remains idempotent: it does not reset a scan, re-open
  bytes, or change resource state. Completion replay still verifies stored bytes.
- Session inventory and revocation require the authenticated owner, the same
  organization, and an active target. Even platform administrators cannot revoke
  another user's sessions. Bulk revocation keeps these predicates in SQL.
- Existing owner locks serialize session lifecycle changes and re-read live
  authority. Document mutations hold the document lock through policy evaluation
  and commit; attempt initiation now flushes before its final commit to retain it.

These are intentional tightenings: previously an existing upload could be used
following a rejected scan or a non-intake state, and verified expired/failed
attempts could complete. They now return 409 `DOCUMENT_NOT_WRITABLE` or
`UPLOAD_NOT_WRITABLE`. Missing role authority remains 403 `FORBIDDEN`.

No policy editor, user role mutation API, delegation, document ownership model,
branch/department scope, or compliance certification is implied.

## Verification

`tests/test_access_policy.py` covers all seven persisted roles, the full document
state/scan matrix, unknown actions/roles, and owner/tenant/active session decisions.
`tests/test_access_policy_http.py` exercises the real ASGI HTTP app with DB-backed
sessions: capability truth, forged headers, live role changes, invalid roles,
resource-state changes, expired completion/renewal, cross-tenant 404 equivalence,
and same-tenant foreign session denial even for platform administrators.
Existing auth, intake, and concurrent session suites remain regression coverage.

Run `python -m pytest -q`. Set `DOCUMENT_TEST_DATABASE_URL` for policy HTTP/intake
and `TEST_DATABASE_URL` for auth/session suites to an isolated PostgreSQL database.
Fixtures drop/recreate tables: never point them at a shared or production DB.
The Python CI workflow runs the new suite against PostgreSQL 16 as well as the
normal SQLite/default test run. No database migration is needed.
