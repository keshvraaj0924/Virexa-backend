CREATE TABLE IF NOT EXISTS workflow_idempotency_keys (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  request_hash text NOT NULL,
  workflow_id uuid REFERENCES workflows(id) ON DELETE RESTRICT,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, idempotency_key),
  CONSTRAINT workflow_idempotency_key_check CHECK (length(idempotency_key) BETWEEN 16 AND 255),
  CONSTRAINT workflow_idempotency_hash_check CHECK (length(request_hash) = 64)
);

CREATE INDEX IF NOT EXISTS workflow_idempotency_created_idx
  ON workflow_idempotency_keys (created_at);

REVOKE ALL ON workflow_idempotency_keys FROM PUBLIC;
