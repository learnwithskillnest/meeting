#!/usr/bin/env python3
"""Create or promote an administrator account.

Credentials come from the environment (never hard-coded):

    ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='a-long-unique-passphrase' python scripts/create_admin.py

If the account exists it is promoted to admin. Otherwise a verified admin
account (no dating profile) is created. Run this on the server; the action is
written to the audit log.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from heartsync import db as dbm  # noqa: E402
from heartsync import security  # noqa: E402
from heartsync.seed_data import ensure_reference_data  # noqa: E402
from heartsync.services import audit  # noqa: E402


def main() -> int:
    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    role = os.environ.get("ADMIN_ROLE", "admin")
    if role not in ("admin", "moderator"):
        print("ADMIN_ROLE must be admin or moderator", file=sys.stderr)
        return 2
    if not email:
        print("Set ADMIN_EMAIL (and ADMIN_PASSWORD for a new account).", file=sys.stderr)
        return 2
    conn = dbm.connect()
    dbm.migrate(conn)
    ensure_reference_data(conn)
    row = dbm.one(conn, "SELECT id FROM users WHERE email=?", (email,))
    with dbm.tx(conn):
        if row:
            conn.execute("UPDATE users SET role=?, updated_at=? WHERE id=?", (role, dbm.iso(), row["id"]))
            audit.log(conn, None, f"admin.cli_promote_{role}", "user", row["id"])
            print(f"Promoted {email} to {role}.")
            return 0
        problems = security.password_problems(password, email)
        if problems:
            print("ADMIN_PASSWORD rejected: " + " ".join(problems), file=sys.stderr)
            return 2
        uid, now = dbm.new_id(), dbm.iso()
        conn.execute(
            "INSERT INTO users (id, email, password_hash, date_of_birth, role, email_verified_at, terms_accepted_at,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, email, security.hash_password(password), "1990-01-01", role, now, now, now, now))
        conn.execute("INSERT INTO user_contacts (user_id, updated_at) VALUES (?,?)", (uid, now))
        audit.log(conn, None, f"admin.cli_create_{role}", "user", uid)
    print(f"Created {role} account {email}. Sign in at /login and open /admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
