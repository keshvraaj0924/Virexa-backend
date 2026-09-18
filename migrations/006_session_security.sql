ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS last_seen_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS sessions_user_recent_idx
  ON sessions (user_id, created_at DESC)
  WHERE revoked_at IS NULL;
