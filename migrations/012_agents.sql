-- Tenant-scoped AI Agent persistence.
-- Organization remains the authoritative tenant boundary; hierarchy references use
-- composite foreign keys so an agent cannot bind resources from another tenant.
CREATE TABLE IF NOT EXISTS agents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  created_by_user_id uuid NOT NULL,
  name text NOT NULL,
  description text,
  persona_id uuid,
  branch_id uuid,
  department_id uuid,
  instructions text NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT agents_name_check CHECK (length(btrim(name)) BETWEEN 2 AND 120),
  CONSTRAINT agents_description_check CHECK (description IS NULL OR length(description) <= 1000),
  CONSTRAINT agents_instructions_check CHECK (length(btrim(instructions)) BETWEEN 1 AND 12000),
  CONSTRAINT agents_status_check CHECK (status IN ('draft', 'active', 'paused', 'archived')),
  CONSTRAINT agents_version_check CHECK (version > 0),
  CONSTRAINT agents_creator_tenant_fk FOREIGN KEY (organization_id, created_by_user_id)
    REFERENCES users (organization_id, id) ON DELETE RESTRICT,
  CONSTRAINT agents_persona_tenant_fk FOREIGN KEY (organization_id, persona_id)
    REFERENCES personas (organization_id, id) ON DELETE RESTRICT,
  CONSTRAINT agents_branch_tenant_fk FOREIGN KEY (organization_id, branch_id)
    REFERENCES branches (organization_id, id) ON DELETE RESTRICT,
  CONSTRAINT agents_department_tenant_fk FOREIGN KEY (organization_id, department_id)
    REFERENCES departments (organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, id)
);

CREATE INDEX IF NOT EXISTS agents_org_created_idx ON agents (organization_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS agents_org_status_created_idx ON agents (organization_id, status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS agents_org_persona_idx ON agents (organization_id, persona_id) WHERE persona_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agents_org_branch_idx ON agents (organization_id, branch_id) WHERE branch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agents_org_department_idx ON agents (organization_id, department_id) WHERE department_id IS NOT NULL;

REVOKE ALL ON agents FROM PUBLIC;
