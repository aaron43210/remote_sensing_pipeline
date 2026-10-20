-- Refresh tokens (opaque, only their SHA-256 is stored) with rotation families:
-- re-use of a rotated token means it was stolen -> the whole family is revoked.
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    family_id   UUID NOT NULL,
    token_hash  BYTEA NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    rotated_at  TIMESTAMPTZ,
    revoked_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip          TEXT,
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS refresh_family ON refresh_tokens (family_id);
CREATE INDEX IF NOT EXISTS refresh_user ON refresh_tokens (user_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id    BIGINT,
    event      TEXT NOT NULL,      -- login_ok login_fail login_locked register refresh_reuse logout ...
    ip         TEXT,
    user_agent TEXT,
    detail     JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS audit_user ON audit_log (user_id, ts DESC);

-- lets an admin (or a password change) invalidate every access token of a user at once
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INT NOT NULL DEFAULT 0;
