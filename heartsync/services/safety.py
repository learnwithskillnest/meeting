"""Blocking, reporting, automatic risk flags and appeals."""
from __future__ import annotations

import json
import sqlite3

from .. import db as dbm
from .. import security
from ..realtime import hub
from ..web import APIError, CurrentUser, bad_request, not_found, too_many
from . import audit, relations

REPORT_CATEGORIES = {
    "fake_profile": "Fake profile or impersonation",
    "harassment": "Harassment or threats",
    "inappropriate": "Inappropriate or explicit content",
    "scam": "Scam, money request or spam",
    "underage": "May be under 18",
    "hate": "Hate speech or discrimination",
    "off_platform": "Unwanted calls or messages outside the app",
    "offline_behaviour": "Behaviour on a date",
    "other": "Something else",
}
AUTO_FLAG_REPORTERS = 3
AUTO_FLAG_DAYS = 7


def block(conn: sqlite3.Connection, user: CurrentUser, target_id: str) -> None:
    if target_id == user.id:
        raise bad_request("You can't block yourself.")
    if not dbm.one(conn, "SELECT 1 FROM users WHERE id=? AND status!='deleted'", (target_id,)):
        raise not_found("Member not found.")
    with dbm.tx(conn):
        conn.execute("INSERT OR IGNORE INTO blocks (blocker_id, blocked_id, created_at) VALUES (?,?,?)",
                     (user.id, target_id, dbm.iso()))
        # Blocking ends any match and revokes phone sharing in both directions.
        ua, ub = relations.pair(user.id, target_id)
        m = dbm.one(conn, "SELECT id FROM matches WHERE user_a=? AND user_b=?", (ua, ub))
        if m:
            conn.execute("UPDATE matches SET unmatched_at=COALESCE(unmatched_at, ?), unmatched_by=COALESCE(unmatched_by, ?)"
                         " WHERE id=?", (dbm.iso(), user.id, m["id"]))
            conn.execute("UPDATE phone_shares SET revoked_at=? WHERE match_id=? AND revoked_at IS NULL", (dbm.iso(), m["id"]))
            conv = dbm.one(conn, "SELECT id FROM conversations WHERE match_id=?", (m["id"],))
            if conv:
                hub.publish(target_id, "conversation_closed", {"conversation_id": conv["id"]})
        conn.execute("DELETE FROM saved_profiles WHERE (user_id=? AND saved_user_id=?) OR (user_id=? AND saved_user_id=?)",
                     (user.id, target_id, target_id, user.id))
        audit.log(conn, user.id, "safety.block", "user", target_id)


def unblock(conn: sqlite3.Connection, user: CurrentUser, target_id: str) -> None:
    # Unblocking does not restore the match; both people would need to like again.
    with dbm.tx(conn):
        conn.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (user.id, target_id))
        audit.log(conn, user.id, "safety.unblock", "user", target_id)


def blocked_list(conn: sqlite3.Connection, user: CurrentUser) -> list[dict]:
    return [{"user_id": r["blocked_id"], "display_name": r["display_name"] or "Member", "blocked_at": r["created_at"]}
            for r in dbm.all_(conn, "SELECT b.blocked_id, b.created_at, p.display_name FROM blocks b"
                                    " LEFT JOIN profiles p ON p.user_id=b.blocked_id WHERE b.blocker_id=?"
                                    " ORDER BY b.created_at DESC", (user.id,))]


def report(conn: sqlite3.Connection, user: CurrentUser, target_id: str, category: str, details: str | None,
           message_id: int | None, also_block: bool) -> str:
    if category not in REPORT_CATEGORIES:
        raise bad_request("Choose a report reason.", {"category": "Required."})
    if target_id == user.id:
        raise bad_request("You can't report yourself.")
    if not security.allow("report", user.id):
        raise too_many()
    details = (details or "").strip()[:2000] or None
    target = dbm.one(conn, "SELECT id FROM users WHERE id=? AND status!='deleted'", (target_id,))
    if not target:
        raise not_found("Member not found.")
    # Reporter must have a real connection to the target: visible profile,
    # a (past) match, or an incoming like - prevents mass-report abuse.
    ua, ub = relations.pair(user.id, target_id)
    had_match = dbm.one(conn, "SELECT id FROM matches WHERE user_a=? AND user_b=?", (ua, ub))
    if not had_match and not relations.can_view_profile(conn, user.id, target_id) and not relations.is_blocked_either(conn, user.id, target_id):
        raise not_found("Member not found.")
    snapshot = None
    if message_id is not None:
        msg = dbm.one(
            conn,
            "SELECT msg.id, msg.body, msg.kind, msg.created_at, msg.sender_id FROM messages msg"
            " JOIN conversation_participants cp ON cp.conversation_id=msg.conversation_id AND cp.user_id=?"
            " WHERE msg.id=?",
            (user.id, message_id),
        )
        if not msg or msg["sender_id"] != target_id:
            raise not_found("Message not found.")
        keys = [r[0] for r in dbm.all_(conn, "SELECT storage_key FROM message_attachments WHERE message_id=?", (message_id,))]
        snapshot = json.dumps({"message_id": msg["id"], "body": msg["body"], "kind": msg["kind"],
                               "created_at": msg["created_at"], "attachments": keys})
    rid = dbm.new_id()
    with dbm.tx(conn):
        conn.execute(
            "INSERT INTO reports (id, reporter_id, reported_user_id, message_id, category, details, evidence_snapshot, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (rid, user.id, target_id, message_id, category, details, snapshot, dbm.iso()))
        audit.log(conn, user.id, "safety.report", "user", target_id, {"report_id": rid, "category": category})
        _maybe_flag(conn, target_id, category)
    if also_block:
        block(conn, user, target_id)
    return rid


def _maybe_flag(conn: sqlite3.Connection, target_id: str, category: str) -> None:
    reporters = dbm.scalar(
        conn, "SELECT COUNT(DISTINCT reporter_id) FROM reports WHERE reported_user_id=? AND created_at>=?"
              " AND status IN ('open','reviewing')", (target_id, dbm.iso_in(days=-AUTO_FLAG_DAYS)))
    if category == "underage" or reporters >= AUTO_FLAG_REPORTERS:
        reason = "underage report" if category == "underage" else f"{reporters} reports in {AUTO_FLAG_DAYS} days"
        conn.execute("UPDATE users SET flagged_at=COALESCE(flagged_at, ?), flag_reason=COALESCE(flag_reason, ?) WHERE id=?",
                     (dbm.iso(), reason, target_id))
        audit.log(conn, None, "safety.auto_flag", "user", target_id, {"reason": reason})


def my_reports(conn: sqlite3.Connection, user: CurrentUser) -> list[dict]:
    return [{"id": r["id"], "category": REPORT_CATEGORIES.get(r["category"], r["category"]), "status": r["status"],
             "created_at": r["created_at"], "display_name": r["display_name"] or "Member"}
            for r in dbm.all_(conn, "SELECT r.*, p.display_name FROM reports r LEFT JOIN profiles p"
                                    " ON p.user_id=r.reported_user_id WHERE r.reporter_id=? ORDER BY r.created_at DESC LIMIT 50",
                              (user.id,))]


def submit_appeal(conn: sqlite3.Connection, user: CurrentUser, message: str) -> str:
    message = (message or "").strip()
    if user.status != "suspended":
        raise bad_request("Appeals are only available for suspended accounts.")
    if len(message) < 20:
        raise bad_request("Please explain in a little more detail (at least 20 characters).", {"message": "Too short."})
    if dbm.one(conn, "SELECT 1 FROM appeals WHERE user_id=? AND status='open'", (user.id,)):
        raise bad_request("You already have an appeal under review.")
    aid = dbm.new_id()
    with dbm.tx(conn):
        conn.execute("INSERT INTO appeals (id, user_id, message, created_at) VALUES (?,?,?,?)",
                     (aid, user.id, message[:3000], dbm.iso()))
        audit.log(conn, user.id, "safety.appeal_submitted", "appeal", aid)
    return aid


def appeal_status(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = dbm.one(conn, "SELECT status, decision, created_at, reviewed_at FROM appeals WHERE user_id=?"
                        " ORDER BY created_at DESC LIMIT 1", (user_id,))
    return dict(row) if row else None


__all__ = ["block", "unblock", "report", "REPORT_CATEGORIES", "APIError"]
