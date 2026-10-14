# Agent creation idempotency — actor isolation (v1)

## Security change
Idempotent POST /api/v1/agents reservations are uniquely scoped by
`(organization_id, created_by_user_id, idempotency_key)`. The server obtains
organization and creator only from the verified session; clients send only a
random 16–255-character Idempotency-Key. Requests and response envelopes do
not change, so the frontend Agent v1 contract stays compatible.

Prior to this change a second user in the same organization could retry an
identical payload and another user's idempotency key and receive their Agent.
With actor scope, same-key retries work only for the original creator, while
another authorized creator makes an independent reservation and Agent.

## Database migration and controlled rollout
1. Back up PostgreSQL. Validate the `users (organization_id, id)` unique index
   from migration 007 and tenant-composite Agent foreign keys from 012.
2. Inspect `agent_idempotency_keys` for `agent_id IS NULL` (unexpected
   committed reservations). Resolve these explicitly; never invent an actor.
3. Apply migrations in order through `014_agent_idempotency_actor_scope.sql`
   with `psql -v ON_ERROR_STOP=1 -f ...`. Migration 014 wraps its DDL and
   backfill in its own transaction and **aborts/rolls back** on unresolved
   ownership. Do not wrap it in a competing outer transaction.
4. Prevent mixed-version writes: deploy migration 014 and the scoped SQL
   together during controlled write downtime. Previous writers that use
   `ON CONFLICT (organization_id, idempotency_key)` are incompatible with the
   new primary key.
5. Verify same-actor retry (201 then 200), different-actor same-key independent
   creation, different-payload 409, cross-tenant access denial, and rollback of
   invalid replay identities on PostgreSQL 16. Test simultaneous inserts.
6. Retain PR #67's migration-all / strict test-outcome CI gate. Do not promote
   Agent PR #66 or enable the frontend Agent workspace until full tests pass.

## Operational considerations
Mutation audit events remain associated with authenticated actors; idempotent
replays must not emit duplicate `agent.created` events. The new actor index
supports scoped key investigations. Do not log raw idempotency keys, instructions
or session credentials. The frontend must not accept tenant selectors.
