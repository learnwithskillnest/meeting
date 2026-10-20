"""In-app notifications (+ optional email), with dedupe and preferences."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from .. import db as dbm
from ..config import get_settings
from ..realtime import hub

log = logging.getLogger("heartsync.notify")

PREF_DEFAULTS = {
    "inapp_likes": 1, "inapp_messages": 1, "email_matches": 1, "email_messages": 0,
    "email_marketing": 0, "quiet_start": None, "quiet_end": None,
}


def get_prefs(conn: sqlite3.Connection, user_id: str) -> dict:
    row = dbm.one(conn, "SELECT * FROM notification_preferences WHERE user_id=?", (user_id,))
    if not row:
        return dict(PREF_DEFAULTS)
    return {k: row[k] for k in PREF_DEFAULTS}


def set_prefs(conn: sqlite3.Connection, user_id: str, prefs: dict) -> dict:
    current = get_prefs(conn, user_id)
    current.update({k: v for k, v in prefs.items() if k in PREF_DEFAULTS})
    conn.execute(
        "INSERT INTO notification_preferences (user_id, inapp_likes, inapp_messages, email_matches,"
        " email_messages, email_marketing, quiet_start, quiet_end, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(user_id) DO UPDATE SET inapp_likes=excluded.inapp_likes,"
        " inapp_messages=excluded.inapp_messages, email_matches=excluded.email_matches,"
        " email_messages=excluded.email_messages, email_marketing=excluded.email_marketing,"
        " quiet_start=excluded.quiet_start, quiet_end=excluded.quiet_end, updated_at=excluded.updated_at",
        (user_id, int(bool(current["inapp_likes"])), int(bool(current["inapp_messages"])),
         int(bool(current["email_matches"])), int(bool(current["email_messages"])),
         int(bool(current["email_marketing"])), current["quiet_start"], current["quiet_end"], dbm.iso()),
    )
    return current


def _in_quiet_hours(prefs: dict, now: datetime | None = None) -> bool:
    start, end = prefs.get("quiet_start"), prefs.get("quiet_end")
    if not start or not end:
        return False
    # Quiet hours are evaluated in IST (launch market) for simplicity.
    from datetime import timedelta, timezone

    now = (now or dbm.utcnow()).astimezone(timezone(timedelta(hours=5, minutes=30)))
    hm = now.strftime("%H:%M")
    return (start <= hm < end) if start < end else (hm >= start or hm < end)


def notify(conn: sqlite3.Connection, user_id: str, kind: str, title: str, body: str | None = None,
           link: str | None = None, dedupe_key: str | None = None, email: bool = False) -> int | None:
    prefs = get_prefs(conn, user_id)
    if kind == "like" and not prefs["inapp_likes"]:
        return None
    if kind == "message" and not prefs["inapp_messages"]:
        return None
    cur = conn.execute(
        "INSERT OR IGNORE INTO notifications (user_id, kind, title, body, link, dedupe_key, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (user_id, kind, title, body, link, dedupe_key, dbm.iso()),
    )
    if cur.rowcount == 0:
        return None  # duplicate suppressed
    nid = cur.lastrowid
    hub.publish(user_id, "notification", {"id": nid, "kind": kind, "title": title, "link": link})
    if email and not _in_quiet_hours(prefs):
        want = (kind == "match" and prefs["email_matches"]) or (kind == "message" and prefs["email_messages"])
        if want or kind in ("moderation", "subscription", "verification"):
            _email_user(conn, user_id, title, link)
    return nid


def _email_user(conn: sqlite3.Connection, user_id: str, title: str, link: str | None) -> None:
    from ..providers.messaging import DeliveryError, send_email

    row = dbm.one(conn, "SELECT email FROM users WHERE id=? AND status!='deleted'", (user_id,))
    if not row:
        return
    s = get_settings()
    # No message contents in email by default - just a pointer back to the app.
    body = f"{title}\n\nOpen {s.brand_name}: {s.base_url}{link or '/'}\n\nManage email preferences: {s.base_url}/settings/notifications\n"
    try:
        send_email(row["email"], f"{s.brand_name}: {title}", body, conn=conn)
    except DeliveryError as exc:
        log.warning("notification email failed: %s", exc)


def unread_count(conn: sqlite3.Connection, user_id: str) -> int:
    return dbm.scalar(conn, "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (user_id,))
