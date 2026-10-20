"""Likes, passes, super likes, undo, mutual matching, unmatching, boosts,
incoming likes and saved profiles."""
from __future__ import annotations

import sqlite3

from .. import db as dbm
from .. import security
from ..realtime import hub
from ..web import APIError, CurrentUser, bad_request, not_found, too_many
from . import audit, billing, relations
from .notifications import notify

UNDO_WINDOW_MIN = 10


def _today_start() -> str:
    now = dbm.utcnow()
    return dbm.iso(now.replace(hour=0, minute=0, second=0, microsecond=0))


def usage(conn: sqlite3.Connection, user_id: str) -> dict:
    ent = billing.entitlements(conn, user_id)
    since = _today_start()
    likes = dbm.scalar(conn, "SELECT COUNT(*) FROM swipes WHERE from_user=? AND kind IN ('like','superlike')"
                             " AND created_at >= ?", (user_id, since))
    supers = dbm.scalar(conn, "SELECT COUNT(*) FROM swipes WHERE from_user=? AND kind='superlike' AND created_at >= ?",
                        (user_id, since))
    month_start = dbm.iso(dbm.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    boosts = dbm.scalar(conn, "SELECT COUNT(*) FROM boosts WHERE user_id=? AND started_at >= ?", (user_id, month_start))
    return {
        "likes_used": likes, "likes_limit": ent["daily_likes"],
        "superlikes_used": supers, "superlikes_limit": ent["daily_superlikes"],
        "boosts_used": boosts, "boosts_limit": ent["monthly_boosts"],
        "plan": ent["plan"],
    }


def swipe(conn: sqlite3.Connection, user: CurrentUser, target_id: str, kind: str) -> dict:
    if kind not in ("like", "superlike", "pass"):
        raise bad_request("Unknown action.")
    if target_id == user.id:
        raise bad_request("You can't like yourself (but we appreciate the confidence).")
    if not security.allow("swipe", user.id):
        raise too_many("You're swiping very fast. Take a breath and try again in a minute.")
    if not relations.can_view_profile(conn, user.id, target_id):
        raise not_found("This profile isn't available.")
    with dbm.tx(conn):
        existing = dbm.one(conn, "SELECT kind FROM swipes WHERE from_user=? AND to_user=?", (user.id, target_id))
        if existing:
            # Idempotent: repeating an action never creates duplicates.
            match = relations.active_match(conn, user.id, target_id)
            return {"kind": existing["kind"], "matched": bool(match), "match_id": match["id"] if match else None,
                    "duplicate": True}
        if kind != "pass":
            u = usage(conn, user.id)
            if u["likes_limit"] is not None and u["likes_used"] >= u["likes_limit"]:
                raise APIError(402, "quota_exceeded",
                               "You've used all your likes for today. They reset at midnight UTC — or upgrade for unlimited likes.",
                               {"feature": "daily_likes"})
            if kind == "superlike" and u["superlikes_used"] >= u["superlikes_limit"]:
                raise APIError(402, "quota_exceeded", "You're out of Super Likes for today.",
                               {"feature": "daily_superlikes"})
        conn.execute("INSERT INTO swipes (from_user, to_user, kind, created_at) VALUES (?,?,?,?)",
                     (user.id, target_id, kind, dbm.iso()))
        result = {"kind": kind, "matched": False, "match_id": None, "duplicate": False}
        if kind == "pass":
            return result
        reciprocal = dbm.one(conn, "SELECT kind FROM swipes WHERE from_user=? AND to_user=? AND kind IN ('like','superlike')",
                             (target_id, user.id))
        if reciprocal:
            match_id = _create_match(conn, user.id, target_id)
            result.update(matched=True, match_id=match_id)
        else:
            ent_target = billing.entitlements(conn, target_id)
            title = "Someone super liked you!" if kind == "superlike" else "Someone likes you"
            notify(conn, target_id, "like", title,
                   "See who it is" if ent_target["see_incoming_likes"] else "Upgrade to see who, or keep swiping to find out.",
                   "/likes", dedupe_key=f"like:{user.id}")
    return result


def _create_match(conn: sqlite3.Connection, a: str, b: str) -> str:
    ua, ub = relations.pair(a, b)
    existing = dbm.one(conn, "SELECT id, unmatched_at FROM matches WHERE user_a=? AND user_b=?", (ua, ub))
    if existing:
        return existing["id"]
    mid, cid, now = dbm.new_id(), dbm.new_id(), dbm.iso()
    conn.execute("INSERT INTO matches (id, user_a, user_b, created_at) VALUES (?,?,?,?)", (mid, ua, ub, now))
    conn.execute("INSERT INTO conversations (id, match_id, created_at) VALUES (?,?,?)", (cid, mid, now))
    conn.executemany("INSERT INTO conversation_participants (conversation_id, user_id) VALUES (?,?)",
                     [(cid, ua), (cid, ub)])
    names = {r["user_id"]: r["display_name"] for r in dbm.all_(
        conn, "SELECT user_id, display_name FROM profiles WHERE user_id IN (?,?)", (ua, ub))}
    for me, other in ((ua, ub), (ub, ua)):
        notify(conn, me, "match", f"It's a match with {names.get(other, 'someone new')}!",
               "Say hello — you can now chat.", f"/chat/{cid}", dedupe_key=f"match:{mid}", email=True)
        hub.publish(me, "match", {"match_id": mid, "conversation_id": cid, "name": names.get(other)})
    audit.log(conn, a, "match.created", "match", mid)
    return mid


def undo(conn: sqlite3.Connection, user: CurrentUser) -> dict:
    billing.require_entitlement(conn, user.id, "undo")
    with dbm.tx(conn):
        last = dbm.one(conn, "SELECT to_user, kind, created_at FROM swipes WHERE from_user=? ORDER BY created_at DESC LIMIT 1",
                       (user.id,))
        if not last or last["created_at"] < dbm.iso_in(minutes=-UNDO_WINDOW_MIN):
            raise bad_request("There's nothing recent to undo.")
        if relations.active_match(conn, user.id, last["to_user"]):
            raise bad_request("You already matched — unmatch instead if you've changed your mind.")
        conn.execute("DELETE FROM swipes WHERE from_user=? AND to_user=?", (user.id, last["to_user"]))
    return {"restored_user_id": last["to_user"], "kind": last["kind"]}


def unmatch(conn: sqlite3.Connection, user: CurrentUser, match_id: str) -> None:
    with dbm.tx(conn):
        m = dbm.one(conn, "SELECT * FROM matches WHERE id=? AND (user_a=? OR user_b=?) AND unmatched_at IS NULL",
                    (match_id, user.id, user.id))
        if not m:
            raise not_found("Match not found.")
        conn.execute("UPDATE matches SET unmatched_at=?, unmatched_by=? WHERE id=?", (dbm.iso(), user.id, match_id))
        conn.execute("UPDATE phone_shares SET revoked_at=? WHERE match_id=? AND revoked_at IS NULL", (dbm.iso(), match_id))
        other = m["user_b"] if m["user_a"] == user.id else m["user_a"]
        conv = dbm.one(conn, "SELECT id FROM conversations WHERE match_id=?", (match_id,))
        if conv:
            hub.publish(other, "conversation_closed", {"conversation_id": conv["id"]})
        audit.log(conn, user.id, "match.unmatched", "match", match_id)


def matches(conn: sqlite3.Connection, user: CurrentUser) -> list[dict]:
    from .profiles import photo_list

    rows = dbm.all_(
        conn,
        "SELECT m.id AS match_id, m.created_at, c.id AS conversation_id, c.last_message_at,"
        " CASE WHEN m.user_a=:me THEN m.user_b ELSE m.user_a END AS other_id"
        " FROM matches m JOIN conversations c ON c.match_id=m.id"
        " WHERE (m.user_a=:me OR m.user_b=:me) AND m.unmatched_at IS NULL"
        " ORDER BY COALESCE(c.last_message_at, m.created_at) DESC",
        {"me": user.id},
    )
    out = []
    for r in rows:
        if relations.is_blocked_either(conn, user.id, r["other_id"]):
            continue
        p = dbm.one(conn, "SELECT p.display_name, u.date_of_birth, u.status FROM profiles p JOIN users u ON u.id=p.user_id"
                          " WHERE p.user_id=?", (r["other_id"],))
        if not p or p["status"] not in ("active",):
            continue
        from .accounts import age_on

        photos = photo_list(conn, r["other_id"])
        out.append({"match_id": r["match_id"], "conversation_id": r["conversation_id"], "user_id": r["other_id"],
                    "display_name": p["display_name"], "age": age_on(p["date_of_birth"]),
                    "photo": photos[0]["url"] if photos else None, "matched_at": r["created_at"],
                    "has_messages": bool(r["last_message_at"])})
    return out


def incoming_likes(conn: sqlite3.Connection, user: CurrentUser) -> dict:
    ent = billing.entitlements(conn, user.id)
    rows = dbm.all_(
        conn,
        "SELECT s.from_user, s.kind, s.created_at FROM swipes s JOIN users u ON u.id=s.from_user"
        " JOIN profiles p ON p.user_id=u.id"
        " WHERE s.to_user=:me AND s.kind IN ('like','superlike') AND u.status='active'"
        " AND NOT EXISTS (SELECT 1 FROM swipes s2 WHERE s2.from_user=:me AND s2.to_user=s.from_user)"
        " AND NOT EXISTS (SELECT 1 FROM blocks b WHERE (b.blocker_id=:me AND b.blocked_id=s.from_user)"
        "   OR (b.blocker_id=s.from_user AND b.blocked_id=:me))"
        " ORDER BY s.kind='superlike' DESC, s.created_at DESC LIMIT 100",
        {"me": user.id},
    )
    if not ent["see_incoming_likes"]:
        # Free plan: count only, no identities (enforced server-side).
        return {"count": len(rows), "visible": False, "profiles": [],
                "superlikes": sum(1 for r in rows if r["kind"] == "superlike")}
    from .profiles import public_profile

    profiles = []
    for r in rows[:50]:
        try:
            prof = public_profile(conn, user.id, r["from_user"], record_view=False)
        except APIError:
            continue
        prof["liked_at"] = r["created_at"]
        prof["superlike"] = r["kind"] == "superlike"
        profiles.append(prof)
    return {"count": len(rows), "visible": True, "profiles": profiles,
            "superlikes": sum(1 for r in rows if r["kind"] == "superlike")}


def boost(conn: sqlite3.Connection, user: CurrentUser) -> dict:
    u = usage(conn, user.id)
    if not u["boosts_limit"] or u["boosts_used"] >= u["boosts_limit"]:
        raise APIError(402, "upgrade_required", "You don't have any boosts left this month.", {"feature": "monthly_boosts"})
    with dbm.tx(conn):
        current = dbm.one(conn, "SELECT boost_until FROM profiles WHERE user_id=?", (user.id,))
        if current and current["boost_until"] and current["boost_until"] > dbm.iso():
            raise bad_request("Your profile is already boosted.")
        ends = dbm.iso_in(minutes=30)
        conn.execute("INSERT INTO boosts (id, user_id, started_at, ends_at) VALUES (?,?,?,?)",
                     (dbm.new_id(), user.id, dbm.iso(), ends))
        conn.execute("UPDATE profiles SET boost_until=? WHERE user_id=?", (ends, user.id))
    return {"boost_until": ends}


def save_profile(conn: sqlite3.Connection, user: CurrentUser, target_id: str, saved: bool) -> None:
    if saved:
        if not relations.can_view_profile(conn, user.id, target_id) or target_id == user.id:
            raise not_found("This profile isn't available.")
        conn.execute("INSERT OR IGNORE INTO saved_profiles (user_id, saved_user_id, created_at) VALUES (?,?,?)",
                     (user.id, target_id, dbm.iso()))
    else:
        conn.execute("DELETE FROM saved_profiles WHERE user_id=? AND saved_user_id=?", (user.id, target_id))


def saved_profiles(conn: sqlite3.Connection, user: CurrentUser) -> list[dict]:
    from .profiles import public_profile

    out = []
    for r in dbm.all_(conn, "SELECT saved_user_id FROM saved_profiles WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
                      (user.id,)):
        try:
            out.append(public_profile(conn, user.id, r[0], record_view=False))
        except APIError:
            continue
    return out
