"""Shared relationship checks (blocks, matches, eligibility).

These are the single source of truth used by discovery, profiles, matching,
chat and phone sharing so restrictions are applied consistently.
"""
from __future__ import annotations

import sqlite3

from .. import db as dbm
from ..config import get_settings


def pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def is_blocked_either(conn: sqlite3.Connection, a: str, b: str) -> bool:
    return bool(dbm.one(
        conn,
        "SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
        (a, b, b, a),
    ))


def active_match(conn: sqlite3.Connection, a: str, b: str) -> sqlite3.Row | None:
    ua, ub = pair(a, b)
    return dbm.one(conn, "SELECT * FROM matches WHERE user_a=? AND user_b=? AND unmatched_at IS NULL", (ua, ub))


# SQL fragment: user `alias` is an eligible, dateable member. Bind order: none.
def eligible_sql(alias: str = "u", profile_alias: str = "p") -> str:
    phone = (
        f" AND EXISTS (SELECT 1 FROM user_contacts c WHERE c.user_id={alias}.id AND c.phone_verified_at IS NOT NULL)"
        if get_settings().require_phone_verification else ""
    )
    return (
        f"{alias}.status='active' AND {alias}.email_verified_at IS NOT NULL"
        f" AND {profile_alias}.onboarded_at IS NOT NULL{phone}"
    )


def not_blocked_sql(viewer_param: str = ":viewer", alias: str = "u") -> str:
    return (
        f"NOT EXISTS (SELECT 1 FROM blocks b WHERE (b.blocker_id={viewer_param} AND b.blocked_id={alias}.id)"
        f" OR (b.blocker_id={alias}.id AND b.blocked_id={viewer_param}))"
    )


def can_view_profile(conn: sqlite3.Connection, viewer_id: str, target_id: str) -> bool:
    if viewer_id == target_id:
        return True
    if is_blocked_either(conn, viewer_id, target_id):
        return False
    row = dbm.one(
        conn,
        f"SELECT p.visibility FROM users u JOIN profiles p ON p.user_id=u.id WHERE u.id=? AND {eligible_sql()}",
        (target_id,),
    )
    if not row:
        return False
    if row["visibility"] == "visible":
        return True
    # Hidden profiles remain visible to existing matches and to people they liked.
    if active_match(conn, viewer_id, target_id):
        return True
    return bool(dbm.one(conn, "SELECT 1 FROM swipes WHERE from_user=? AND to_user=? AND kind!='pass'",
                        (target_id, viewer_id)))
