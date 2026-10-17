# Runnable Python authentication foundation

This is an opt-in FastAPI candidate built from main 21fbab914b34a5499b37a3ff7c518465caa4cf2b. It preserves the Node/Fastify runtime and adds a runnable Python slice, rather than replacing existing routes. Do not point it at an existing Fastify/customer database.

## Run against an isolated PostgreSQL database

Python 3.12 recommended. Provision a new, empty development database first.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
export DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@localhost/virexa_python_dev'
export ALLOWED_ORIGINS='http://localhost:3000'
python -m alembic upgrade head
python -m uvicorn app.main:create_app --factory --port 4000
```

Use `ENVIRONMENT=production` only with HTTPS origins and HTTPS transport. Secure cookies are mandatory in that mode. Do not enable proxy trust indiscriminately: the local limiter uses the ASGI client address. Database tables are never created or migrated on HTTP startup.

## Implemented contract

- POST `/api/v1/auth/register`: displayName, email, password (12–128 characters), organizationName. Returns 201, data.user (id, email, displayName, role, organizationId, organizationName) and data.expiresAt.
- POST `/api/v1/auth/login`: email and password, same response data.
- GET `/api/v1/auth/session`: authenticated session or 401.
- GET `/api/v1/me`: session plus the existing v1 frontend permission vocabulary; does not enable unported endpoints.
- POST `/api/v1/auth/logout`: server-side revocation, cookie deletion, data.success.
- GET `/api/v1/organizations/{organization_id}`: SQL-scoped organization read, requiring user:read. Unknown and other-tenant organizations both return 404. Roles including platform_admin do not bypass tenant boundaries.
- GET `/health` and `/ready`: process liveness and real SELECT 1 readiness.

All browser writes require a trusted Origin. Clients use credentials: include; tokens never appear in JSON. Cookies are HttpOnly and SameSite=Lax, Secure in production. Auth and organization successes/errors are non-cacheable. Passwords use genuine pwdlib Argon2; only opaque session-token SHA-256 digests are persisted. Session organization ownership is pinned at issuance; moving a user to another organization invalidates their old session.

The existing v1 email-only login is kept: new registrations require a globally unique normalized email. A legacy case-variant duplicate email causes generic INVALID_CREDENTIALS rather than choosing an arbitrary organization. Registration retains the existing EMAIL_ALREADY_REGISTERED contract (and therefore reveals that an email is registered). No tenant identifier or role from request JSON is accepted. Python organization_admin and tenant_admin map to wire admin, and platform_admin maps to super_admin; auditor remains a Python-only role outside this UI's supported role set.

## Reconciliation and migration boundary

Reused main's password/session helpers, RBAC map, response envelopes, SQLAlchemy identity models and request schemas. Fixed eager settings/engine imports, snake_case request mismatch, ambiguous email resolution, tenant-unpinned sessions and missing API entrypoint. Existing Node code is unchanged. The new migration extends the existing Python-only migration chain. It refuses duplicate exact email rows through a uniqueness constraint instead of deleting/merging accounts. Review and reconcile legacy normalized-email duplicates before adoption.

P109's saved report was inspected. Its sandbox-only archive and exact-P108 patch could not be materialized in this workspace; none of its source or claimed test counts are treated as inherited verification. This candidate does not incorporate or supersede P109's session-inventory implementation.

## Verification

```sh
python -m pytest -q
python -m ruff check app tests/test_fastapi_auth.py alembic/env.py alembic/versions/0003_auth_identity_boundaries.py
# Destructive test setup: use ONLY a disposable database; tests recreate these tables.
TEST_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@localhost/virexa_test' python -m pytest -q tests/test_fastapi_auth.py
```

The new workflow checks out the exact head commit, runs Python tests, applies migrations to isolated PostgreSQL 16, and repeats the HTTP isolation/auth tests against PostgreSQL. It is a separate gate from legacy Node CI; a legacy green job is not evidence for this Python slice.

## Explicit release blockers / next increments

This is not production-ready or a Fastify cutover. Production adoption needs reviewed non-destructive schema reconciliation, complete route/role parity, transactionally durable audit events, bounded session inventory/rotation policies, distributed rate limiting, real concurrent revocation/authorization analysis, restricted-role PostgreSQL/RLS validation, dependency locking, and same-host HTTPS browser verification. ABAC, RAG retrieval, workers and tenant hierarchy beyond the organization boundary are not implemented here. No background jobs, API keys, external services, customer data migration, merges or deployments are part of this change.

## Frontend deployment topology

Next.js server-side session checks need the session cookie on the frontend host. Deploy behind a same-host reverse proxy (for example the frontend and `/api/v1` under one HTTPS hostname). The cookie is deliberately host-only; unrelated `app.example` and `api.example` hosts will otherwise cause SSR login loops. Localhost on different ports shares the cookie hostname and can be used for local development. The frontend's full protected dashboard still depends on Fastify routes outside this candidate; do not switch it wholesale to this auth-only API.
