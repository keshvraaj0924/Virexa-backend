CREATE TABLE IF NOT EXISTS document_upload_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  document_id uuid NOT NULL,
  idempotency_key text NOT NULL,
  object_key text NOT NULL,
  status text NOT NULL DEFAULT 'initiated'
    CHECK (status IN ('initiated', 'completed', 'failed')),
  failure_code text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT document_upload_attempts_document_tenant_fk
    FOREIGN KEY (organization_id, document_id)
    REFERENCES documents (organization_id, id)
    ON DELETE CASCADE,
  CONSTRAINT document_upload_attempts_idempotency_key_check
    CHECK (length(btrim(idempotency_key)) BETWEEN 1 AND 255),
  CONSTRAINT document_upload_attempts_object_key_check
    CHECK (length(btrim(object_key)) BETWEEN 1 AND 1024),
  CONSTRAINT document_upload_attempts_failure_code_check
    CHECK (failure_code IS NULL OR length(failure_code) <= 100),
  CONSTRAINT document_upload_attempts_completion_check CHECK (
    (status = 'completed' AND completed_at IS NOT NULL AND failure_code IS NULL)
    OR (status = 'failed' AND completed_at IS NULL AND failure_code IS NOT NULL)
    OR (status = 'initiated' AND completed_at IS NULL AND failure_code IS NULL)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS document_upload_attempts_org_idempotency_uq
  ON document_upload_attempts (organization_id, idempotency_key);

CREATE UNIQUE INDEX IF NOT EXISTS document_upload_attempts_org_object_key_active_uq
  ON document_upload_attempts (organization_id, object_key)
  WHERE status = 'initiated';

CREATE INDEX IF NOT EXISTS document_upload_attempts_org_document_created_idx
  ON document_upload_attempts (organization_id, document_id, created_at DESC);

COMMENT ON TABLE document_upload_attempts IS
  'Tenant-scoped durable state for idempotent document binary upload orchestration. Organization scope is server-authoritative.';