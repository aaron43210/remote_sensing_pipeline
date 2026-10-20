"""Create (or reset the password of) the admin account from ADMIN_EMAIL / ADMIN_PASSWORD."""

import os
import sys

import bcrypt
import psycopg


def main():
    email, password = os.getenv("ADMIN_EMAIL", "").strip().lower(), os.getenv("ADMIN_PASSWORD", "")
    if not email or len(password) < 12:
        print("ADMIN_EMAIL / ADMIN_PASSWORD (12+ characters) not set; skipping admin bootstrap")
        return 0
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("""INSERT INTO users (email, password_hash, name, role) VALUES (%s, %s, 'Administrator', 'admin')
                        ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = 'admin'""",
                     (email, hashed))
    print("admin account ready:", email)
    return 0


if __name__ == "__main__":
    sys.exit(main())
