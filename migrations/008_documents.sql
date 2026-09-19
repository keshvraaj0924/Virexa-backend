CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  branch_id uuid REFERENCES branches(id) ON DELETE RESTRICT,
  department_id uuid REFERENCES departments(id) ON DELETE RESTRICT,
  source text NOT NULL CHECK (source IN ('upload', 'email', 'api', 'integration')),
  external_reference text,
  original_file_name text NOT NULL,
  media_type text NOT NULL,
  size_bytes bigint NOT NULL CHECK (size_bytes > 0),
  checksum_sha256 char(64) NOT NULL CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$'),
  status text NOT NULL DEFAULT 'received' CHECK (status IN ('received', 'processing', 'review_required', 'completed', 'failed')),
  failure_code text,
  received_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT documents_scope_consistency CHECK (
    (branch_id IS NULL OR organization_id IS NOT NULL)
    AND (department_id IS NULL OR organization_id IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS documents_tenant_received_idx
  ON documents (tenant_id, received_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS documents_tenant_status_received_idx
  ON documents (tenant_id, status, received_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS documents_tenant_organization_received_idx
  ON documents (tenant_id, organization_id, received_at DESC, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS documents_tenant_source_external_reference_uq
  ON documents (tenant_id, source, external_reference)
  WHERE external_reference IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS documents_tenant_checksum_uq
  ON documents (tenant_id, checksum_sha256);

COMMENT ON TABLE documents IS 'Tenant-scoped immutable intake identity and processing-state ledger. Binary payloads live in object storage, never in PostgreSQL.';
