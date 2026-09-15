CREATE TABLE IF NOT EXISTS audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  action text NOT NULL,
  resource_type text NOT NULL,
  resource_id text,
  request_id text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT audit_events_action_check CHECK (length(action) BETWEEN 1 AND 120),
  CONSTRAINT audit_events_resource_type_check CHECK (length(resource_type) BETWEEN 1 AND 120)
);

CREATE INDEX IF NOT EXISTS audit_events_org_created_idx
  ON audit_events (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_events_actor_created_idx
  ON audit_events (actor_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS audit_events_resource_idx
  ON audit_events (organization_id, resource_type, resource_id, created_at DESC);

REVOKE ALL ON audit_events FROM PUBLIC;
