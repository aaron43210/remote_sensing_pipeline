-- Each user's NASA Earthdata token (EMIT downloads for their jobs), AES-256-GCM
-- encrypted with CREDENTIALS_KEY and bound to the user. Never returned by the API.
CREATE TABLE IF NOT EXISTS earthdata_tokens (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    ciphertext  BYTEA NOT NULL,                        -- nonce || AES-GCM(token), see pkg/secretbox
    username    TEXT,                                  -- Earthdata user name read from the token, for display
    expires_at  TIMESTAMPTZ,                           -- the token's own expiry (NASA: 60 days)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
