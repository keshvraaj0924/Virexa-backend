-- Tenant-scoped idempotency ledger for AI Agent creation.
-- A key is unique only inside its authenticated organization. The request hash
-- prevents the same key from being reused for a semantically different create.
CREATE TABLE IF NOT EXISTS agent_idempotency_keys (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  request_hash text NOT NULL,
  agent_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, idempotency_key),
  CONSTRAINT agent_idempotency_key_check CHECK (length(idempotency_key) BETWEEN 16 AND 255),
  CONSTRAINT agent_idempotency_hash_check CHECK (length(request_hash) = 64),
  CONSTRAINT agent_idempotency_agent_tenant_fk FOREIGN KEY (organization_id, agent_id)
    REFERENCES agents (organization_id, id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS agent_idempotency_created_idx
  ON agent_idempotency_keys (created_at);

REVOKE ALL ON agent_idempotency_keys FROM PUBLIC;
