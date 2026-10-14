-- Scope Agent creation idempotency to the authenticated organization AND actor.
-- Migration 007 creates the tenant-composite user uniqueness required by the FK.
-- PostgreSQL DDL and backfill are atomic even when invoked through plain psql.
BEGIN;

ALTER TABLE agent_idempotency_keys
  ADD COLUMN created_by_user_id uuid;

-- Every committed reservation should point at an Agent created by its actor.
-- Fail rather than guessing an actor for orphaned/unresolved legacy reservations.
UPDATE agent_idempotency_keys AS ledger
SET created_by_user_id = agent.created_by_user_id
FROM agents AS agent
WHERE ledger.organization_id = agent.organization_id
  AND ledger.agent_id = agent.id;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM agent_idempotency_keys WHERE created_by_user_id IS NULL
  ) THEN
    RAISE EXCEPTION 'Agent idempotency migration blocked: unresolved actor identity';
  END IF;
END
$$;

ALTER TABLE agent_idempotency_keys
  ALTER COLUMN created_by_user_id SET NOT NULL;

ALTER TABLE agent_idempotency_keys
  DROP CONSTRAINT agent_idempotency_keys_pkey;

ALTER TABLE agent_idempotency_keys
  ADD CONSTRAINT agent_idempotency_keys_pkey
    PRIMARY KEY (organization_id, created_by_user_id, idempotency_key);

ALTER TABLE agent_idempotency_keys
  ADD CONSTRAINT agent_idempotency_actor_tenant_fk
    FOREIGN KEY (organization_id, created_by_user_id)
    REFERENCES users (organization_id, id) ON DELETE RESTRICT;

CREATE INDEX agent_idempotency_actor_created_idx
  ON agent_idempotency_keys (organization_id, created_by_user_id, created_at);

COMMIT;
