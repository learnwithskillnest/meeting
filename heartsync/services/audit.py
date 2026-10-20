"""Audit log for security-relevant and administrative actions.

Never put secrets, phone numbers, OTPs or message bodies in metadata.
"""
from __future__ import annotations

import json
import sqlite3

from .. import db as dbm


def log(conn: sqlite3.Connection, actor_id: str | None, action: str, target_type: str | None = None,
        target_id: str | None = None, metadata: dict | None = None, ip_hash: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_logs (actor_id, action, target_type, target_id, metadata, ip_hash, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (actor_id, action, target_type, target_id, json.dumps(metadata or {}), ip_hash, dbm.iso()),
    )
