CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  branch_id uuid,
  department_id uuid,
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
  CONSTRAINT documents_file_name_check CHECK (length(btrim(original_file_name)) BETWEEN 1 AND 512),
  CONSTRAINT documents_media_type_check CHECK (length(btrim(media_type)) BETWEEN 1 AND 255),
  CONSTRAINT documents_external_reference_check CHECK (external_reference IS NULL OR length(external_reference) BETWEEN 1 AND 255),
  CONSTRAINT documents_failure_code_check CHECK (failure_code IS NULL OR length(failure_code) <= 100),
  CONSTRAINT documents_org_id_uq UNIQUE (organization_id, id),
  CONSTRAINT documents_branch_tenant_fk
    FOREIGN KEY (organization_id, branch_id)
    REFERENCES branches (organization_id, id)
    ON DELETE RESTRICT,
  CONSTRAINT documents_department_tenant_fk
    FOREIGN KEY (organization_id, department_id)
    REFERENCES departments (organization_id, id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS documents_org_received_idx
  ON documents (organization_id, received_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS documents_org_status_received_idx
  ON documents (organization_id, status, received_at DESC, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS documents_org_source_external_reference_uq
  ON documents (organization_id, source, external_reference)
  WHERE external_reference IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS documents_org_checksum_uq
  ON documents (organization_id, checksum_sha256);

COMMENT ON TABLE documents IS 'Organization-tenant-scoped immutable intake identity and processing-state ledger. Binary payloads live in object storage, never in PostgreSQL.';