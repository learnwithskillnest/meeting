"""Consent-based phone number sharing between matched members.

* A number is never part of any profile, discovery, search or list response.
* The owner explicitly shares it with ONE matched person (confirmation
  required), and can revoke at any time. Unmatching/blocking revokes.
* The recipient fetches it through a dedicated endpoint that re-checks the
  share, the match and blocks on every request; each reveal is audited
  (without the number itself).
"""
from __future__ import annotations

import sqlite3

from .. import db as dbm
from .. import security
from ..realtime import hub
from ..web import APIError, CurrentUser, bad_request, not_found, too_many
from . import audit, relations
from .notifications import notify


def _match_for(conn: sqlite3.Connection, user: CurrentUser, match_id: str) -> tuple[sqlite3.Row, str]:
    m = dbm.one(conn, "SELECT * FROM matches WHERE id=? AND (user_a=? OR user_b=?) AND unmatched_at IS NULL",
                (match_id, user.id, user.id))
    if not m:
        raise not_found("Match not found.")
    other = m["user_b"] if m["user_a"] == user.id else m["user_a"]
    if relations.is_blocked_either(conn, user.id, other):
        raise not_found("Match not found.")
    return m, other


def _active_share(conn: sqlite3.Connection, owner: str, recipient: str, match_id: str) -> sqlite3.Row | None:
    return dbm.one(conn, "SELECT * FROM phone_shares WHERE owner_id=? AND recipient_id=? AND match_id=? AND revoked_at IS NULL",
                   (owner, recipient, match_id))


def status(conn: sqlite3.Connection, user: CurrentUser, match_id: str) -> dict:
    m, other = _match_for(conn, user, match_id)
    verified = dbm.one(conn, "SELECT phone_verified_at FROM user_contacts WHERE user_id=?", (user.id,))
    return {"i_shared": bool(_active_share(conn, user.id, other, match_id)),
            "they_shared": bool(_active_share(conn, other, user.id, match_id)),
            "can_share": bool(verified and verified["phone_verified_at"])}


def share(conn: sqlite3.Connection, user: CurrentUser, match_id: str, confirm: bool) -> dict:
    if not confirm:
        raise bad_request("Please confirm that you want to share your number.", {"confirm": "Confirmation required."})
    if not security.allow("phone_share", user.id):
        raise too_many()
    with dbm.tx(conn):
        m, other = _match_for(conn, user, match_id)
        contact = dbm.one(conn, "SELECT phone_e164, phone_verified_at FROM user_contacts WHERE user_id=?", (user.id,))
        if not contact or not contact["phone_verified_at"] or not contact["phone_e164"]:
            raise bad_request("Verify your phone number before sharing it.", code="phone_unverified")
        if _active_share(conn, user.id, other, match_id):
            return {"shared": True}
        conn.execute("INSERT INTO phone_shares (id, owner_id, recipient_id, match_id, created_at) VALUES (?,?,?,?,?)",
                     (dbm.new_id(), user.id, other, match_id, dbm.iso()))
        conv = dbm.one(conn, "SELECT id FROM conversations WHERE match_id=?", (match_id,))
        if conv:
            cur = conn.execute("INSERT INTO messages (conversation_id, sender_id, kind, body, created_at) VALUES (?,?,?,?,?)",
                               (conv["id"], user.id, "system", "shared their phone number with you", dbm.iso()))
            conn.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (dbm.iso(), conv["id"]))
            hub.publish(other, "phone_shared", {"conversation_id": conv["id"], "match_id": match_id,
                                                "message_id": cur.lastrowid})
        notify(conn, other, "phone", f"{user.display_name or 'Your match'} shared their phone number",
               "Open the chat to view it.", f"/chat/{conv['id']}" if conv else "/matches",
               dedupe_key=f"phone:{match_id}:{user.id}:{dbm.iso()[:13]}")
        audit.log(conn, user.id, "phone.shared", "match", match_id, {"recipient": other})
    return {"shared": True}


def revoke(conn: sqlite3.Connection, user: CurrentUser, match_id: str) -> None:
    with dbm.tx(conn):
        m = dbm.one(conn, "SELECT * FROM matches WHERE id=? AND (user_a=? OR user_b=?)", (match_id, user.id, user.id))
        if not m:
            raise not_found("Match not found.")
        conn.execute("UPDATE phone_shares SET revoked_at=? WHERE owner_id=? AND match_id=? AND revoked_at IS NULL",
                     (dbm.iso(), user.id, match_id))
        audit.log(conn, user.id, "phone.revoked", "match", match_id)


def reveal(conn: sqlite3.Connection, user: CurrentUser, match_id: str) -> dict:
    m, other = _match_for(conn, user, match_id)
    share_row = _active_share(conn, other, user.id, match_id)
    if not share_row:
        raise APIError(403, "not_shared", "This member hasn't shared their number with you.")
    contact = dbm.one(conn, "SELECT phone_e164, phone_verified_at FROM user_contacts WHERE user_id=?", (other,))
    if not contact or not contact["phone_e164"] or not contact["phone_verified_at"]:
        raise APIError(403, "not_shared", "This number is no longer available.")
    audit.log(conn, user.id, "phone.revealed", "match", match_id, {"owner": other})
    return {"phone": contact["phone_e164"]}
