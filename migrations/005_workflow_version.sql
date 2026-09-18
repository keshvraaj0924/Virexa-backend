ALTER TABLE workflows
  ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;

ALTER TABLE workflows
  DROP CONSTRAINT IF EXISTS workflows_version_check;

ALTER TABLE workflows
  ADD CONSTRAINT workflows_version_check CHECK (version >= 1);
