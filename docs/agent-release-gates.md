# Agent v1 release gate integration

This branch is stacked on `fix/agents-actor-idempotency-scope` (PR #68), itself stacked on Agent HTTP PR #66. It ports the exact release-gate repairs from PR #67 without dropping Agent route registration or authenticated-creator-scoped idempotency.

## Checks to complete before enabling the frontend
1. GitHub Actions on the **exact head commit** must apply every `migrations/[0-9][0-9][0-9]_*.sql` file to PostgreSQL 16 with `ON_ERROR_STOP=1`, including migration 014.
2. Vitest and `node:test` suites must run in distinct runners. Ambiguous tests fail closed; test failure must fail CI on `steps.<id>.outcome`, not `.conclusion`.
3. Verify typecheck and production build in the same workflow. Agent create retries must be isolated by tenant and authenticated creator; cross-actor replay and mismatched body must fail closed.
4. Preserve authorization: operators cannot perform document extraction mutations; rejected operations must not cause persistence or audit writes.
5. Confirm Agent HTTP security tests cover tenant identity from the session, RBAC/ownership, trusted origin, idempotency, version conflicts, and sensitive response cache policy.
6. Keep the frontend Agent workspace disabled until both repositories pass their independent quality gates and frontend/client contracts match backend v1.

## Important deployment ordering
Migration 014 changes Agent idempotency uniqueness from (organization, key) to (organization, creator, key). Pause Agent creation writes while the database migration and corresponding service deployment are coordinated. The migration fails closed if legacy rows cannot be attributed to their creator. Never mix pre-014 code with post-014 writes.

## Local validation
```bash
npm install --no-audit --no-fund
npm run typecheck
npm run test:unit
npm run test:node
npm run build
```

The CI workflow uses PostgreSQL 16. The project does not currently define an npm lint script; do not claim lint success. Run the CI checks before review or merge. No feature branches are merged directly to `main` by this change.
