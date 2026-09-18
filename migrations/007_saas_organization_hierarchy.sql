-- Virexa B2B SaaS organization hierarchy foundation.
-- Organization remains the current tenant security boundary. Child resources carry
-- organization_id explicitly so every query can be tenant-scoped without joins.

CREATE TABLE IF NOT EXISTS branches (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  name text NOT NULL,
  code text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT branches_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 160),
  CONSTRAINT branches_code_check CHECK (length(btrim(code)) BETWEEN 1 AND 64),
  CONSTRAINT branches_status_check CHECK (status IN ('active', 'inactive')),
  UNIQUE (organization_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS branches_org_code_uq
  ON branches (organization_id, lower(code));

CREATE TABLE IF NOT EXISTS departments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  branch_id uuid NOT NULL,
  name text NOT NULL,
  code text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT departments_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 160),
  CONSTRAINT departments_code_check CHECK (length(btrim(code)) BETWEEN 1 AND 64),
  CONSTRAINT departments_status_check CHECK (status IN ('active', 'inactive')),
  CONSTRAINT departments_branch_tenant_fk
    FOREIGN KEY (organization_id, branch_id)
    REFERENCES branches (organization_id, id)
    ON DELETE RESTRICT,
  UNIQUE (organization_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS departments_branch_code_uq
  ON departments (organization_id, branch_id, lower(code));

CREATE TABLE IF NOT EXISTS personas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  department_id uuid NOT NULL,
  name text NOT NULL,
  description text,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT personas_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 160),
  CONSTRAINT personas_description_check CHECK (description IS NULL OR length(description) <= 4000),
  CONSTRAINT personas_status_check CHECK (status IN ('active', 'inactive')),
  CONSTRAINT personas_department_tenant_fk
    FOREIGN KEY (organization_id, department_id)
    REFERENCES departments (organization_id, id)
    ON DELETE RESTRICT,
  UNIQUE (organization_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS personas_department_name_uq
  ON personas (organization_id, department_id, lower(name));

-- Assignments are explicit membership edges. They intentionally do not change
-- the existing organization-level role; server authorization can combine the
-- role with branch/department/persona scope when ABAC endpoints are introduced.
CREATE TABLE IF NOT EXISTS user_branch_assignments (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  user_id uuid NOT NULL,
  branch_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_id, branch_id),
  CONSTRAINT user_branch_user_tenant_fk
    FOREIGN KEY (organization_id, user_id)
    REFERENCES users (organization_id, id)
    ON DELETE CASCADE,
  CONSTRAINT user_branch_branch_tenant_fk
    FOREIGN KEY (organization_id, branch_id)
    REFERENCES branches (organization_id, id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_department_assignments (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  user_id uuid NOT NULL,
  department_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_id, department_id),
  CONSTRAINT user_department_user_tenant_fk
    FOREIGN KEY (organization_id, user_id)
    REFERENCES users (organization_id, id)
    ON DELETE CASCADE,
  CONSTRAINT user_department_department_tenant_fk
    FOREIGN KEY (organization_id, department_id)
    REFERENCES departments (organization_id, id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_persona_assignments (
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  user_id uuid NOT NULL,
  persona_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, user_id, persona_id),
  CONSTRAINT user_persona_user_tenant_fk
    FOREIGN KEY (organization_id, user_id)
    REFERENCES users (organization_id, id)
    ON DELETE CASCADE,
  CONSTRAINT user_persona_persona_tenant_fk
    FOREIGN KEY (organization_id, persona_id)
    REFERENCES personas (organization_id, id)
    ON DELETE CASCADE
);

-- Composite tenant foreign keys above require a matching unique key on users.
-- Adding it here is backward compatible with the existing primary key.
CREATE UNIQUE INDEX IF NOT EXISTS users_org_id_uq
  ON users (organization_id, id);

CREATE INDEX IF NOT EXISTS branches_org_status_idx
  ON branches (organization_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS departments_org_branch_idx
  ON departments (organization_id, branch_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS personas_org_department_idx
  ON personas (organization_id, department_id, status, created_at DESC);

REVOKE ALL ON branches, departments, personas,
  user_branch_assignments, user_department_assignments, user_persona_assignments
  FROM PUBLIC;
