# Python organization member administration

This milestone administers **existing users in the authenticated organization**. It does not invite, create, remove, disable, move, or impersonate users; reset passwords; or add workspaces to an email identity. One email still belongs to one workspace. Individual accounts have no team capabilities and receive 403 on these endpoints, even if their stored role is an administrator.

## Endpoints and envelope

All routes use the normal `{data, meta}` success envelope, `{error, meta}` errors, cookie authentication, and private/no-store headers.

- `GET /api/v1/organization/members?limit=25&cursor=<nextCursor>` returns `{items: Member[], nextCursor: string | null}`. Limit is 1–100. UUID keyset order is stable and ascending; only this tenant is queried. The cursor is opaque to clients, and does not grant access. Empty lists have a null cursor. Pagination is not a snapshot across account creation.
- `GET /api/v1/organization/members/{id}` returns one `Member`; foreign and absent identities both return 404.
- `PATCH /api/v1/organization/members/{id}/role` takes exactly `{role, expectedVersion}` and returns the current `Member`. A trusted Origin header is required. Versions start at 1. `expectedVersion` must be a positive JSON integer; a successful change increments it. A retry with the old version returns 409 without repeating the change or audit. Setting the already-current role at the current version is a no-op. No idempotency key is needed because compare-and-swap and absolute role assignment prevent duplicate effects.
- `GET /api/v1/organization/hierarchy` returns `{organization: {id, name, accountType: "organization"}, branches: [], departments: [], personas: []}`. Only the root exists in the Python model. Empty arrays do **not** imply that legacy Node hierarchy data was imported. Branch/department/persona management is unavailable in this candidate.

`Member` contains exactly `id`, `organizationId`, `displayName`, `email`, `role`, `version`, and `createdAt` (timezone-aware ISO 8601). Passwords, password hashes, cookies, and session tokens are never included.

## Capabilities and roles

`GET /api/v1/me/permissions` advertises effective capabilities from the current database user and workspace:

| Capability | Organization roles |
| --- | --- |
| `organization:members:read` | platform_admin, tenant_admin, organization_admin, auditor |
| `organization:members:manage` | platform_admin, tenant_admin, organization_admin |
| `organization:hierarchy:read` | platform_admin, tenant_admin, organization_admin, auditor |

Emails are visible only to these authorized roster readers within their own tenant. Managers, operators, and viewers cannot read the roster. Platform admins have no cross-tenant exception.

Assignable roles are `tenant_admin`, `organization_admin`, `manager`, `operator`, `viewer`, and `auditor`. `platform_admin` is not assignable; existing platform-admin targets cannot be modified. Every self role change is rejected. There is no separate owner/disabled/member-status column in the current identity model, so this feature does not imply ownership transfer or fabricate active/inactive status. Every persisted tenant/organization admin counts toward the last-admin invariant, independently of whether they currently have a login session.

Role writes lock the organization row, refresh the actor's role and account type **after** acquiring the lock, and then enforce the expected target version. They preserve at least one `tenant_admin` or `organization_admin` (a platform admin is not a replacement). Concurrent reciprocal demotions cannot both succeed. Existing sessions resolve the live user role on their next request, so changed capabilities take effect without waiting for expiry.

Errors include 400 validation/invalid cursor, 401 unauthenticated, 403 `FORBIDDEN`, 403 `UNTRUSTED_ORIGIN`, 403 `SELF_ROLE_CHANGE_FORBIDDEN`, 404 `NOT_FOUND`, 409 `MEMBER_CONFLICT`, and 409 `LAST_ORGANIZATION_ADMIN`. Reload before retrying a version conflict; do not silently overwrite a newer role.

## Audit and deployment

A real role change records `organization.member.role_updated`, resource type `user`, opaque user ID, server request correlation, and only `{outcome: "succeeded"}` metadata, in the same transaction. No email, name, old/new role, or arbitrary client metadata is logged. Failed audit insertion rolls back the role change. No-op or stale repeated requests produce no success event.

Run Alembic migration `0009_member_versions` in the **separate Python database** before starting this version. It adds a non-null member version default of 1. This is an additive candidate migration; no production deployment or Node database migration is included.

Tests cover role/capability matrices, individual accounts, all assignable roles, same-tenant data minimization, pagination, foreign-ID equivalence, self/platform protection, last-admin protection, version conflicts, session freshness, audit rollback, and real PostgreSQL reciprocal/concurrent demotions.
