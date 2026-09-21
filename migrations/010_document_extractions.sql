CREATE TABLE document_extractions (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL,
  document_id uuid NOT NULL,
  status text NOT NULL CHECK (status IN ('queued','processing','review_required','completed','failed')),
  schema_version varchar(64) NOT NULL,
  fields jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(fields) = 'array'),
  failure_code varchar(100),
  idempotency_key varchar(255) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT document_extractions_document_tenant_fk
    FOREIGN KEY (organization_id, document_id)
    REFERENCES documents (organization_id, id)
    ON DELETE CASCADE,
  CONSTRAINT document_extractions_idempotency_unique
    UNIQUE (organization_id, document_id, idempotency_key)
);

CREATE INDEX document_extractions_tenant_document_created_idx
  ON document_extractions (organization_id, document_id, created_at DESC, id DESC);

CREATE INDEX document_extractions_tenant_status_created_idx
  ON document_extractions (organization_id, status, created_at DESC, id DESC);

REVOKE ALL ON TABLE document_extractions FROM PUBLIC;
