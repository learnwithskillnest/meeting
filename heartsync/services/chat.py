"""Conversations and messages between mutually matched members.

Every operation goes through `_conversation_for`, which checks that the
caller is a participant, the match is still active and neither side has
blocked the other. A wrong or foreign conversation id always yields 404.
"""
from __future__ import annotations

import re
import sqlite3

from .. import db as dbm
from .. import security
from ..config import get_settings
from ..providers import storage
from ..realtime import hub
from ..web import APIError, CurrentUser, bad_request, not_found, too_many
from . import billing, relations
from .notifications import notify

EDIT_WINDOW_MIN = 15
ALLOWED_REACTIONS = ["❤️", "😂", "😮", "😢", "👍", "🔥"]
URL_RE = re.compile(r"https?://|www\.", re.I)


def _conversation_for(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str,
                      require_open: bool = True) -> dict:
    row = dbm.one(
        conn,
        "SELECT c.id, c.match_id, m.user_a, m.user_b, m.unmatched_at, cp.muted, cp.archived, cp.last_read_id"
        " FROM conversations c JOIN matches m ON m.id=c.match_id"
        " JOIN conversation_participants cp ON cp.conversation_id=c.id AND cp.user_id=?"
        " WHERE c.id=?",
        (user.id, conversation_id),
    )
    if not row:
        raise not_found("Conversation not found.")
    other = row["user_b"] if row["user_a"] == user.id else row["user_a"]
    if require_open:
        if row["unmatched_at"] or relations.is_blocked_either(conn, user.id, other):
            raise not_found("Conversation not found.")
        status = dbm.scalar(conn, "SELECT status FROM users WHERE id=?", (other,))
        if status != "active":
            raise APIError(410, "unavailable", "This member is no longer available.")
    return {**dict(row), "other_id": other}


def _serialize(conn: sqlite3.Connection, row: sqlite3.Row, viewer_id: str, show_receipts: bool) -> dict:
    attachments = []
    if row["kind"] == "image" and not row["deleted_at"]:
        for a in dbm.all_(conn, "SELECT id, storage_key, mime, width, height FROM message_attachments WHERE message_id=?",
                          (row["id"],)):
            attachments.append({"id": a["id"], "url": storage.signed_url(a["storage_key"]), "mime": a["mime"],
                                "width": a["width"], "height": a["height"]})
    reactions = [{"user_id": r["user_id"], "emoji": r["emoji"]} for r in dbm.all_(
        conn, "SELECT user_id, emoji FROM message_reactions WHERE message_id=?", (row["id"],))]
    reply = None
    if row["reply_to_id"]:
        rr = dbm.one(conn, "SELECT id, body, kind, deleted_at, sender_id FROM messages WHERE id=? AND conversation_id=?",
                     (row["reply_to_id"], row["conversation_id"]))
        if rr:
            reply = {"id": rr["id"], "sender_id": rr["sender_id"],
                     "body": None if rr["deleted_at"] else (rr["body"] or ("Photo" if rr["kind"] == "image" else ""))[:140]}
    mine = row["sender_id"] == viewer_id
    return {
        "id": row["id"], "conversation_id": row["conversation_id"], "sender_id": row["sender_id"], "mine": mine,
        "kind": row["kind"], "body": None if row["deleted_at"] else row["body"], "deleted": bool(row["deleted_at"]),
        "edited": bool(row["edited_at"]), "created_at": row["created_at"], "reply_to": reply,
        "attachments": attachments, "reactions": reactions,
        "status": ("read" if row["read_at"] and show_receipts else "delivered" if row["delivered_at"] else "sent")
        if mine else None,
    }


def list_conversations(conn: sqlite3.Connection, user: CurrentUser, q: str | None = None,
                       archived: bool = False) -> list[dict]:
    from .accounts import age_on
    from .profiles import photo_list

    rows = dbm.all_(
        conn,
        "SELECT c.id, c.last_message_at, c.created_at, cp.muted, cp.archived, cp.last_read_id,"
        " CASE WHEN m.user_a=:me THEN m.user_b ELSE m.user_a END AS other_id"
        " FROM conversation_participants cp JOIN conversations c ON c.id=cp.conversation_id"
        " JOIN matches m ON m.id=c.match_id"
        " WHERE cp.user_id=:me AND m.unmatched_at IS NULL AND cp.archived=:arch"
        " ORDER BY COALESCE(c.last_message_at, c.created_at) DESC LIMIT 200",
        {"me": user.id, "arch": int(archived)},
    )
    out = []
    needle = (q or "").strip().lower()
    for r in rows:
        if relations.is_blocked_either(conn, user.id, r["other_id"]):
            continue
        p = dbm.one(conn, "SELECT p.display_name, p.show_online_status, u.date_of_birth, u.status FROM profiles p"
                          " JOIN users u ON u.id=p.user_id WHERE p.user_id=?", (r["other_id"],))
        if not p or p["status"] != "active":
            continue
        last = dbm.one(conn, "SELECT id, sender_id, kind, body, deleted_at, created_at FROM messages"
                             " WHERE conversation_id=? ORDER BY id DESC LIMIT 1", (r["id"],))
        if needle:
            name_hit = needle in p["display_name"].lower()
            body_hit = bool(dbm.one(conn, "SELECT 1 FROM messages WHERE conversation_id=? AND deleted_at IS NULL"
                                          " AND lower(body) LIKE ? LIMIT 1", (r["id"], f"%{needle}%")))
            if not (name_hit or body_hit):
                continue
        unread = dbm.scalar(conn, "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND id>? AND sender_id!=?"
                                  " AND deleted_at IS NULL", (r["id"], r["last_read_id"], user.id))
        photos = photo_list(conn, r["other_id"])
        preview = None
        if last:
            preview = ("Message deleted" if last["deleted_at"] else "📷 Photo" if last["kind"] == "image"
                       else (last["body"] or "")[:80])
            if last["sender_id"] == user.id:
                preview = "You: " + preview
        out.append({
            "id": r["id"], "user_id": r["other_id"], "display_name": p["display_name"], "age": age_on(p["date_of_birth"]),
            "photo": photos[0]["url"] if photos else None, "last_message": preview,
            "last_message_at": r["last_message_at"] or r["created_at"], "unread": unread,
            "muted": bool(r["muted"]), "archived": bool(r["archived"]),
            "online": bool(p["show_online_status"]) and hub.is_online(r["other_id"]),
        })
    return out


def total_unread(conn: sqlite3.Connection, user_id: str) -> int:
    return dbm.scalar(
        conn,
        "SELECT COUNT(*) FROM messages msg JOIN conversation_participants cp ON cp.conversation_id=msg.conversation_id"
        " JOIN conversations c ON c.id=cp.conversation_id JOIN matches m ON m.id=c.match_id"
        " WHERE cp.user_id=:me AND msg.id>cp.last_read_id AND msg.sender_id!=:me AND msg.deleted_at IS NULL"
        " AND m.unmatched_at IS NULL AND cp.muted=0"
        " AND NOT EXISTS (SELECT 1 FROM blocks b WHERE (b.blocker_id=:me AND b.blocked_id=msg.sender_id)"
        "   OR (b.blocker_id=msg.sender_id AND b.blocked_id=:me))",
        {"me": user_id},
    ) or 0


def get_messages(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str, before_id: int | None = None,
                 after_id: int | None = None, limit: int = 30) -> dict:
    conv = _conversation_for(conn, user, conversation_id)
    limit = max(1, min(limit, 100))
    show_receipts = bool(billing.entitlements(conn, user.id).get("read_receipts"))
    if after_id is not None:
        rows = dbm.all_(conn, "SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id ASC LIMIT ?",
                        (conversation_id, after_id, limit))
        has_more = False
    else:
        rows = dbm.all_(conn, "SELECT * FROM messages WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT ?",
                        (conversation_id, before_id or 2**62, limit + 1))
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
    # Mark the other person's messages as delivered.
    undelivered = [r["id"] for r in rows if r["sender_id"] == conv["other_id"] and not r["delivered_at"]]
    if undelivered:
        q = ",".join("?" * len(undelivered))
        conn.execute(f"UPDATE messages SET delivered_at=? WHERE id IN ({q})", (dbm.iso(), *undelivered))
    from .profiles import public_profile

    other = public_profile(conn, user.id, conv["other_id"], record_view=False)
    return {
        "conversation": {"id": conversation_id, "match_id": conv["match_id"], "muted": bool(conv["muted"]),
                         "archived": bool(conv["archived"]), "other": other},
        "messages": [_serialize(conn, r, user.id, show_receipts) for r in rows],
        "has_more": has_more,
        "reactions": ALLOWED_REACTIONS,
    }


def _check_body(body: str | None) -> str:
    s = get_settings()
    body = (body or "").strip()
    if not body:
        raise bad_request("Message can't be empty.", {"body": "Required."})
    if len(body) > s.max_message_chars:
        raise bad_request(f"Messages are limited to {s.max_message_chars} characters.", {"body": "Too long."})
    if len(URL_RE.findall(body)) > 3:
        raise bad_request("That message has too many links.", {"body": "Too many links."}, code="spam_suspected")
    return body


def _after_send(conn: sqlite3.Connection, user: CurrentUser, conv: dict, msg_row: sqlite3.Row) -> dict:
    conn.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (msg_row["created_at"], conv["id"]))
    conn.execute("UPDATE conversation_participants SET last_read_id=?, archived=0 WHERE conversation_id=? AND user_id=?",
                 (msg_row["id"], conv["id"], user.id))
    conn.execute("UPDATE conversation_participants SET archived=0 WHERE conversation_id=? AND user_id=?",
                 (conv["id"], conv["other_id"]))
    other_part = dbm.one(conn, "SELECT muted, last_read_id FROM conversation_participants WHERE conversation_id=? AND user_id=?",
                         (conv["id"], conv["other_id"]))
    other_muted = other_part["muted"]
    other_receipts = bool(billing.entitlements(conn, conv["other_id"]).get("read_receipts"))
    hub.publish(conv["other_id"], "message", _serialize(conn, msg_row, conv["other_id"], other_receipts))
    if not other_muted:
        name = user.display_name or "Your match"
        # Notification never includes message contents.
        notify(conn, conv["other_id"], "message", f"New message from {name}", None, f"/chat/{conv['id']}",
               # One notification per conversation until the recipient reads it.
               dedupe_key=f"msg:{conv['id']}:{other_part['last_read_id']}", email=False)
    show = bool(billing.entitlements(conn, user.id).get("read_receipts"))
    return _serialize(conn, msg_row, user.id, show)


def send_message(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str, body: str,
                 reply_to_id: int | None = None, client_id: str | None = None) -> dict:
    if not security.allow("message", user.id):
        raise too_many("You're sending messages too quickly. Please slow down.")
    body = _check_body(body)
    if client_id and (len(client_id) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", client_id)):
        raise bad_request("Invalid client id.")
    with dbm.tx(conn):
        conv = _conversation_for(conn, user, conversation_id)
        if client_id:
            dup = dbm.one(conn, "SELECT * FROM messages WHERE sender_id=? AND client_id=?", (user.id, client_id))
            if dup:
                return _serialize(conn, dup, user.id, False)
        if reply_to_id is not None and not dbm.one(
                conn, "SELECT 1 FROM messages WHERE id=? AND conversation_id=?", (reply_to_id, conversation_id)):
            raise bad_request("The message you're replying to isn't in this conversation.")
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, sender_id, kind, body, reply_to_id, client_id, created_at)"
            " VALUES (?,?,?,?,?,?,?)", (conversation_id, user.id, "text", body, reply_to_id, client_id, dbm.iso()))
        row = dbm.one(conn, "SELECT * FROM messages WHERE id=?", (cur.lastrowid,))
        return _after_send(conn, user, conv, row)


def send_image(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str, data: bytes,
               client_id: str | None = None) -> dict:
    if not security.allow("upload", user.id) or not security.allow("message", user.id):
        raise too_many("You're sending too quickly. Please slow down.")
    _conversation_for(conn, user, conversation_id)  # authorize before touching storage
    try:
        stored = storage.save_image(f"chat/{conversation_id}", data)
    except storage.UploadRejected as exc:
        raise bad_request(str(exc), code="upload_rejected")
    try:
        with dbm.tx(conn):
            conv = _conversation_for(conn, user, conversation_id)
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, sender_id, kind, body, client_id, created_at) VALUES (?,?,?,?,?,?)",
                (conversation_id, user.id, "image", None, client_id, dbm.iso()))
            conn.execute(
                "INSERT INTO message_attachments (id, message_id, storage_key, mime, size_bytes, width, height)"
                " VALUES (?,?,?,?,?,?,?)",
                (dbm.new_id(), cur.lastrowid, stored.key, stored.mime, stored.size, stored.width, stored.height))
            row = dbm.one(conn, "SELECT * FROM messages WHERE id=?", (cur.lastrowid,))
            return _after_send(conn, user, conv, row)
    except Exception:
        storage.delete(stored.key)
        raise


def _own_message(conn: sqlite3.Connection, user: CurrentUser, message_id: int) -> tuple[sqlite3.Row, dict]:
    row = dbm.one(conn, "SELECT * FROM messages WHERE id=?", (message_id,))
    if not row:
        raise not_found("Message not found.")
    conv = _conversation_for(conn, user, row["conversation_id"])
    return row, conv


def edit_message(conn: sqlite3.Connection, user: CurrentUser, message_id: int, body: str) -> dict:
    body = _check_body(body)
    with dbm.tx(conn):
        row, conv = _own_message(conn, user, message_id)
        if row["sender_id"] != user.id or row["kind"] != "text" or row["deleted_at"]:
            raise APIError(403, "forbidden", "You can only edit your own text messages.")
        if row["created_at"] < dbm.iso_in(minutes=-EDIT_WINDOW_MIN):
            raise bad_request(f"Messages can only be edited within {EDIT_WINDOW_MIN} minutes of sending.")
        conn.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (body, dbm.iso(), message_id))
        row = dbm.one(conn, "SELECT * FROM messages WHERE id=?", (message_id,))
        hub.publish(conv["other_id"], "message_updated", _serialize(conn, row, conv["other_id"], False))
        return _serialize(conn, row, user.id, False)


def delete_message(conn: sqlite3.Connection, user: CurrentUser, message_id: int) -> None:
    keys = []
    with dbm.tx(conn):
        row, conv = _own_message(conn, user, message_id)
        if row["sender_id"] != user.id:
            raise APIError(403, "forbidden", "You can only delete your own messages.")
        if row["deleted_at"]:
            return
        keys = [r[0] for r in dbm.all_(conn, "SELECT storage_key FROM message_attachments WHERE message_id=?", (message_id,))]
        # Keep an evidence snapshot only if the message is already under report.
        conn.execute("UPDATE messages SET body=NULL, deleted_at=? WHERE id=?", (dbm.iso(), message_id))
        reported = dbm.one(conn, "SELECT 1 FROM reports WHERE message_id=?", (message_id,))
        if not reported:
            conn.execute("DELETE FROM message_attachments WHERE message_id=?", (message_id,))
        else:
            keys = []
        conn.execute("DELETE FROM message_reactions WHERE message_id=?", (message_id,))
        hub.publish(conv["other_id"], "message_deleted", {"id": message_id, "conversation_id": conv["id"]})
    for k in keys:
        storage.delete(k)


def react(conn: sqlite3.Connection, user: CurrentUser, message_id: int, emoji: str | None) -> list[dict]:
    if emoji is not None and emoji not in ALLOWED_REACTIONS:
        raise bad_request("That reaction isn't available.")
    with dbm.tx(conn):
        row, conv = _own_message(conn, user, message_id)
        if row["deleted_at"]:
            raise bad_request("You can't react to a deleted message.")
        if emoji is None:
            conn.execute("DELETE FROM message_reactions WHERE message_id=? AND user_id=?", (message_id, user.id))
        else:
            conn.execute("INSERT INTO message_reactions (message_id, user_id, emoji, created_at) VALUES (?,?,?,?)"
                         " ON CONFLICT(message_id, user_id) DO UPDATE SET emoji=excluded.emoji",
                         (message_id, user.id, emoji, dbm.iso()))
        reactions = [{"user_id": r[0], "emoji": r[1]} for r in dbm.all_(
            conn, "SELECT user_id, emoji FROM message_reactions WHERE message_id=?", (message_id,))]
        hub.publish(conv["other_id"], "reaction", {"message_id": message_id, "conversation_id": conv["id"],
                                                    "reactions": reactions})
    return reactions


def mark_read(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str, up_to_id: int) -> None:
    with dbm.tx(conn):
        conv = _conversation_for(conn, user, conversation_id)
        max_id = dbm.scalar(conn, "SELECT MAX(id) FROM messages WHERE conversation_id=?", (conversation_id,)) or 0
        up_to = min(int(up_to_id), max_id)
        conn.execute("UPDATE conversation_participants SET last_read_id=MAX(last_read_id, ?) WHERE conversation_id=? AND user_id=?",
                     (up_to, conversation_id, user.id))
        now = dbm.iso()
        conn.execute("UPDATE messages SET read_at=?, delivered_at=COALESCE(delivered_at, ?) WHERE conversation_id=?"
                     " AND sender_id=? AND id<=? AND read_at IS NULL", (now, now, conversation_id, conv["other_id"], up_to))
    if billing.entitlements(conn, conv["other_id"]).get("read_receipts"):
        hub.publish(conv["other_id"], "read", {"conversation_id": conversation_id, "up_to_id": up_to})


def typing(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str) -> None:
    if not security.allow("typing", user.id):
        return
    conv = _conversation_for(conn, user, conversation_id)
    hub.publish(conv["other_id"], "typing", {"conversation_id": conversation_id})


def set_flags(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str, muted: bool | None,
              archived: bool | None) -> None:
    _conversation_for(conn, user, conversation_id, require_open=False)
    if muted is not None:
        conn.execute("UPDATE conversation_participants SET muted=? WHERE conversation_id=? AND user_id=?",
                     (int(muted), conversation_id, user.id))
    if archived is not None:
        conn.execute("UPDATE conversation_participants SET archived=? WHERE conversation_id=? AND user_id=?",
                     (int(archived), conversation_id, user.id))


def icebreakers(conn: sqlite3.Connection, user: CurrentUser, conversation_id: str) -> list[str]:
    """Rule-based suggestions (clearly labelled as suggestions; never auto-sent)."""
    conv = _conversation_for(conn, user, conversation_id)
    shared = [r[0] for r in dbm.all_(
        conn, "SELECT i.label FROM profile_interests a JOIN profile_interests b ON a.interest_id=b.interest_id"
              " JOIN interests i ON i.id=a.interest_id WHERE a.user_id=? AND b.user_id=? LIMIT 3",
        (user.id, conv["other_id"]))]
    ideas = [f"I noticed we both like {s.lower()} — how did you get into it?" for s in shared]
    ideas += ["What's the best thing that happened to you this week?",
              "If you could plan a perfect Sunday, what would it look like?",
              "What's a place you'd love to travel to next?"]
    return ideas[:4]
