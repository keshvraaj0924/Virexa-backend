# Python workflow tracking records

This opt-in Python milestone ports the existing Node `/api/v1/workflows` contract.
It stores tracking metadata. `active` means the record's configured lifecycle
state, not that a process is running. Creation and edits never execute steps,
call external integrations, send email, invoke a model, or meter `workflow_runs`.
There is no execution runner, action registry, arbitrary-code/eval interface,
webhook trigger, schedule, integration credential store, or run endpoint. Steps,
actions, scripts and integration configuration are unknown fields and rejected.
A future action registry must define bounded, validated schemas before accepting
any executable definition; this release intentionally accepts no action JSON.

## Compatibility and API

Responses use the normal `{data, meta}` envelope. Workflow fields are `id`,
`organizationId`, `createdByUserId`, `name`, nullable `description`, `status`,
`version`, `createdAt`, and `updatedAt`. Both personal and organization workspaces
use their authenticated organization ID as the isolation boundary. There is no
company-email or domain assumption and no client-supplied tenant scope.

- GET `/api/v1/workflows`: `status`, `limit` (1–100, default 25), opaque `cursor`.
  Returns `{items, nextCursor}`. Stable descending created-time/UUID pagination.
- POST: `{name, description?}` with `Idempotency-Key` (trimmed, 16–255 characters).
  Creates `draft`, version 1. First response 201, same normalized request/key 200;
  conflicting payload 409 `IDEMPOTENCY_KEY_REUSED`. Keys are tenant-scoped like
  Node, not user-scoped. A replay returns the current stored workflow, including
  subsequent edits, without another audit event. Names trim to 1–120 characters;
  descriptions trim to at most 1,000 and empty creation descriptions become null.
- GET `/{id}`: record or generic 404; foreign IDs are indistinguishable from missing.
- PATCH `/{id}`: `{expectedVersion, name?, description?, status?}`. At least one
  field is required. Version is a positive integer; explicit null only clears
  description. Every successful patch increments version, including same-status
  requests. Stale writes return 409 `WORKFLOW_CONFLICT` without partial changes.
- No DELETE or execution endpoint. All JSON accepts only registered fields and
  the request stream is bounded to 32 KiB before JSON parsing. Mutation requests
  require a trusted origin. Responses remain private/no-store.

Lifecycle: draft → active; active → paused or archived; paused → active or
archived. Same-state edits are allowed. Archived records cannot reopen, but their
name/description can still be edited, matching the Node contract. Invalid state
changes return 409 `INVALID_STATE_TRANSITION`.

## Authoritative policy and transactions

Capabilities come from `/me/permissions` and actual database roles. Operators can
read/create records and edit their own. Managers, organization admins, tenant
admins and platform admins can edit any record within their authenticated tenant.
Viewers read only; auditors have audit access but no workflow capability. No
role has a cross-tenant bypass. The old wire-role catalogue is not authorization.

A unique tenant/key reservation and database transaction serialize concurrent
creation across processes. Atomic conditional updates check tenant, ID, version,
owner and observed status. Tenant-qualified composite foreign keys bind creators
and idempotency references to the same tenant. `workflow.created` and
`workflow.updated` audit events commit in the same business transaction; audit
failure rolls back the mutation. Audit payloads contain only opaque IDs and an
allowlisted outcome, never workflow descriptions, names or idempotency keys.

Migration `0008_workflow_records` follows `0007_audit_events` in the separate
Python database. Do not run the Python migration chain over a Node-managed
production database. Startup does not create tables. No legacy rows or historical
audit events are manufactured. Node contracts remain unchanged.

## Verification

`tests/test_workflows.py` runs HTTP validation, lifecycle, pagination, tenancy and
all-role ownership tests on SQLite and PostgreSQL. PostgreSQL-specific tests cover
competing creates, competing versioned updates and audit-outage rollback.
The Python CI runs migration/schema checks and the existing signup, auth, intake,
session, usage, access-policy and audit regressions against PostgreSQL 16.
