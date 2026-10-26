-- Sign-in is Google only (OpenID Connect): no passwords are stored any more.
-- google_sub is Google's stable account id; accounts are matched on it, not on email.
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE users DROP COLUMN IF EXISTS password_hash;
