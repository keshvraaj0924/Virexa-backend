CREATE TABLE IF NOT EXISTS workflows (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  created_by_user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  name text NOT NULL,
  description text,
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT workflows_name_check CHECK (length(trim(name)) BETWEEN 1 AND 160),
  CONSTRAINT workflows_status_check CHECK (status IN ('draft', 'active', 'paused', 'archived'))
);

CREATE INDEX IF NOT EXISTS workflows_organization_created_idx
  ON workflows (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS workflows_organization_status_idx
  ON workflows (organization_id, status);

REVOKE ALL ON workflows FROM PUBLIC;
