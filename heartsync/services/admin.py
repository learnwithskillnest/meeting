"""Administrative and moderation operations. Every state change is audited."""
from __future__ import annotations

import json
import sqlite3

from .. import db as dbm
from ..config import get_settings
from ..providers import storage
from ..realtime import hub
from ..web import CurrentUser, bad_request, forbidden, not_found
from . import audit
from .accounts import age_on, mask_phone
from .notifications import notify
from .safety import REPORT_CATEGORIES

PAGE = 25


def _page(page: int) -> tuple[int, int]:
    page = max(1, int(page or 1))
    return PAGE, (page - 1) * PAGE


def metrics(conn: sqlite3.Connection) -> dict:
    d7, d1, d30 = dbm.iso_in(days=-7), dbm.iso_in(days=-1), dbm.iso_in(days=-30)
    q = lambda sql, *p: dbm.scalar(conn, sql, p) or 0  # noqa: E731
    subs = {r[0]: r[1] for r in dbm.all_(
        conn, "SELECT plan, COUNT(*) FROM subscriptions WHERE status IN ('active','canceled','past_due')"
              " AND current_period_end > ? GROUP BY plan", (dbm.iso(),))}
    return {
        "users_total": q("SELECT COUNT(*) FROM users WHERE status!='deleted'"),
        "users_active_7d": q("SELECT COUNT(*) FROM users WHERE last_active_at>=? AND status='active'", d7),
        "users_new_7d": q("SELECT COUNT(*) FROM users WHERE created_at>=?", d7),
        "profiles_onboarded": q("SELECT COUNT(*) FROM profiles WHERE onboarded_at IS NOT NULL"),
        "matches_7d": q("SELECT COUNT(*) FROM matches WHERE created_at>=?", d7),
        "messages_24h": q("SELECT COUNT(*) FROM messages WHERE created_at>=?", d1),
        "likes_24h": q("SELECT COUNT(*) FROM swipes WHERE created_at>=? AND kind!='pass'", d1),
        "reports_open": q("SELECT COUNT(*) FROM reports WHERE status IN ('open','reviewing')"),
        "verifications_pending": q("SELECT COUNT(*) FROM photo_verifications WHERE status='pending'"),
        "appeals_open": q("SELECT COUNT(*) FROM appeals WHERE status='open'"),
        "flagged_users": q("SELECT COUNT(*) FROM users WHERE flagged_at IS NOT NULL AND status='active'"),
        "suspended_users": q("SELECT COUNT(*) FROM users WHERE status='suspended'"),
        "subscriptions": subs,
        "revenue_30d_minor": q("SELECT COALESCE(SUM(amount_minor),0) FROM payments WHERE status='succeeded' AND created_at>=?", d30),
        "realtime_connections": hub.connection_count(),
    }


def spam_indicators(conn: sqlite3.Connection) -> list[dict]:
    """Heuristic signals for review; never automatic proof of wrongdoing."""
    d1 = dbm.iso_in(days=-1)
    rows = dbm.all_(conn, """
        SELECT u.id, u.email, p.display_name,
          (SELECT COUNT(*) FROM swipes s WHERE s.from_user=u.id AND s.kind!='pass' AND s.created_at>=:d1) AS likes_24h,
          (SELECT COUNT(*) FROM messages m WHERE m.sender_id=u.id AND m.created_at>=:d1) AS msgs_24h,
          (SELECT COUNT(DISTINCT m.conversation_id) FROM messages m WHERE m.sender_id=u.id AND m.created_at>=:d1) AS convs_24h,
          (SELECT COUNT(*) FROM reports r WHERE r.reported_user_id=u.id) AS reports_total,
          (SELECT COUNT(*) FROM messages m WHERE m.sender_id=u.id AND m.created_at>=:d1
             AND (m.body LIKE '%http%' OR m.body LIKE '%www.%')) AS links_24h
        FROM users u LEFT JOIN profiles p ON p.user_id=u.id
        WHERE u.status='active' AND u.last_active_at>=:d1
    """, {"d1": d1})
    out = []
    for r in rows:
        signals = []
        if r["likes_24h"] > 150:
            signals.append(f"{r['likes_24h']} likes in 24h")
        if r["convs_24h"] > 15:
            signals.append(f"Messaged {r['convs_24h']} conversations in 24h")
        if r["links_24h"] > 5:
            signals.append(f"{r['links_24h']} messages with links")
        if r["reports_total"] >= 2:
            signals.append(f"{r['reports_total']} reports")
        if signals:
            out.append({"user_id": r["id"], "display_name": r["display_name"], "email": r["email"], "signals": signals})
    # Possible duplicate accounts: same hashed IP creating several accounts.
    dupes = dbm.all_(conn, "SELECT ip_hash, COUNT(DISTINCT actor_id) AS n FROM audit_logs WHERE action='account.register'"
                           " AND ip_hash IS NOT NULL AND created_at>=? GROUP BY ip_hash HAVING n>=3",
                     (dbm.iso_in(days=-7),))
    for d in dupes:
        out.append({"user_id": None, "display_name": None, "email": None,
                    "signals": [f"{d['n']} accounts registered from one network in 7 days (ref {d['ip_hash'][:8]})"]})
    return out


def list_users(conn: sqlite3.Connection, q: str | None, status: str | None, flagged: bool, page: int) -> dict:
    limit, offset = _page(page)
    where, params = ["1=1"], []
    if q:
        where.append("(u.email LIKE ? OR p.display_name LIKE ? OR u.id = ?)")
        params += [f"%{q.lower()}%", f"%{q}%", q]
    if status in ("active", "suspended", "deactivated", "deleted"):
        where.append("u.status=?")
        params.append(status)
    if flagged:
        where.append("u.flagged_at IS NOT NULL")
    sql_where = " AND ".join(where)
    total = dbm.scalar(conn, f"SELECT COUNT(*) FROM users u LEFT JOIN profiles p ON p.user_id=u.id WHERE {sql_where}", tuple(params))
    rows = dbm.all_(conn, f"SELECT u.id, u.email, u.role, u.status, u.created_at, u.last_active_at, u.flagged_at,"
                          f" u.email_verified_at, p.display_name,"
                          f" (SELECT COUNT(*) FROM reports r WHERE r.reported_user_id=u.id) AS reports"
                          f" FROM users u LEFT JOIN profiles p ON p.user_id=u.id WHERE {sql_where}"
                          f" ORDER BY u.created_at DESC LIMIT ? OFFSET ?", (*params, limit, offset))
    return {"users": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, -(-total // limit))}


def user_detail(conn: sqlite3.Connection, user_id: str) -> dict:
    u = dbm.one(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if not u:
        raise not_found("User not found.")
    c = dbm.one(conn, "SELECT phone_e164, phone_verified_at FROM user_contacts WHERE user_id=?", (user_id,))
    p = dbm.one(conn, "SELECT * FROM profiles WHERE user_id=?", (user_id,))
    photos = [{"id": r["id"], "url": storage.signed_url(r["storage_key"])} for r in dbm.all_(
        conn, "SELECT id, storage_key FROM profile_photos WHERE user_id=? ORDER BY position", (user_id,))]
    from .billing import current_plan

    return {
        "user": {k: u[k] for k in ("id", "email", "role", "status", "created_at", "last_active_at", "email_verified_at",
                                   "suspended_reason", "flagged_at", "flag_reason", "deleted_at")},
        "age": age_on(u["date_of_birth"]) if u["status"] != "deleted" else None,
        # Admins see a masked number only; full numbers are never displayed.
        "phone": mask_phone(c["phone_e164"]) if c and c["phone_e164"] else None,
        "phone_verified": bool(c and c["phone_verified_at"]),
        "profile": {k: p[k] for k in ("display_name", "headline", "about", "city", "gender", "visibility",
                                      "photo_verified", "completeness", "onboarded_at")} if p else None,
        "photos": photos,
        "plan": current_plan(conn, user_id),
        "reports_against": [dict(r) for r in dbm.all_(
            conn, "SELECT id, category, status, created_at FROM reports WHERE reported_user_id=? ORDER BY created_at DESC LIMIT 50",
            (user_id,))],
        "reports_made": dbm.scalar(conn, "SELECT COUNT(*) FROM reports WHERE reporter_id=?", (user_id,)),
        "actions": [dict(r) for r in dbm.all_(
            conn, "SELECT a.action, a.reason, a.created_at, p.display_name AS actor FROM moderation_actions a"
                  " LEFT JOIN profiles p ON p.user_id=a.actor_id WHERE a.target_user_id=? ORDER BY a.created_at DESC",
            (user_id,))],
    }


def _mod_action(conn, actor: CurrentUser, target_id: str, action: str, reason: str | None, report_id: str | None = None):
    conn.execute("INSERT INTO moderation_actions (id, actor_id, target_user_id, action, reason, report_id, created_at)"
                 " VALUES (?,?,?,?,?,?,?)", (dbm.new_id(), actor.id, target_id, action, reason, report_id, dbm.iso()))
    audit.log(conn, actor.id, f"admin.{action}", "user", target_id, {"reason": reason, "report_id": report_id})


def suspend(conn: sqlite3.Connection, actor: CurrentUser, target_id: str, reason: str, report_id: str | None = None) -> None:
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise bad_request("Give a reason (shown to the member).", {"reason": "Required."})
    with dbm.tx(conn):
        target = dbm.one(conn, "SELECT role, status FROM users WHERE id=?", (target_id,))
        if not target or target["status"] == "deleted":
            raise not_found("User not found.")
        if target["role"] != "user" and not actor.is_admin:
            raise forbidden("Only admins can suspend staff accounts.")
        if target_id == actor.id:
            raise bad_request("You can't suspend yourself.")
        conn.execute("UPDATE users SET status='suspended', suspended_reason=?, updated_at=? WHERE id=?",
                     (reason[:500], dbm.iso(), target_id))
        conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (dbm.iso(), target_id))
        _mod_action(conn, actor, target_id, "suspend", reason, report_id)
        notify(conn, target_id, "moderation", "Your account has been suspended",
               "You can read the reason and submit an appeal.", "/account/suspended", email=True)


def reinstate(conn: sqlite3.Connection, actor: CurrentUser, target_id: str, reason: str | None) -> None:
    with dbm.tx(conn):
        if not dbm.one(conn, "SELECT 1 FROM users WHERE id=? AND status='suspended'", (target_id,)):
            raise bad_request("This account isn't suspended.")
        conn.execute("UPDATE users SET status='active', suspended_reason=NULL, flagged_at=NULL, flag_reason=NULL,"
                     " updated_at=? WHERE id=?", (dbm.iso(), target_id))
        _mod_action(conn, actor, target_id, "reinstate", reason)
        notify(conn, target_id, "moderation", "Your account has been reinstated", "Welcome back.", "/discover", email=True)


def clear_flag(conn: sqlite3.Connection, actor: CurrentUser, target_id: str) -> None:
    with dbm.tx(conn):
        conn.execute("UPDATE users SET flagged_at=NULL, flag_reason=NULL WHERE id=?", (target_id,))
        _mod_action(conn, actor, target_id, "clear_flag", None)


def warn(conn: sqlite3.Connection, actor: CurrentUser, target_id: str, reason: str, report_id: str | None = None) -> None:
    with dbm.tx(conn):
        _mod_action(conn, actor, target_id, "warn", reason, report_id)
        notify(conn, target_id, "moderation", "A message from our safety team",
               "Your recent activity was reported and reviewed. Please review our community guidelines.",
               "/guidelines", email=True)


def set_role(conn: sqlite3.Connection, actor: CurrentUser, target_id: str, role: str) -> None:
    if not actor.is_admin:
        raise forbidden()
    if role not in ("user", "moderator", "admin"):
        raise bad_request("Unknown role.")
    if target_id == actor.id:
        raise bad_request("You can't change your own role.")
    with dbm.tx(conn):
        if not dbm.one(conn, "SELECT 1 FROM users WHERE id=? AND status='active'", (target_id,)):
            raise not_found("User not found.")
        conn.execute("UPDATE users SET role=?, updated_at=? WHERE id=?", (role, dbm.iso(), target_id))
        _mod_action(conn, actor, target_id, f"role_{role}", None)


def list_reports(conn: sqlite3.Connection, status: str, page: int) -> dict:
    limit, offset = _page(page)
    statuses = ("open", "reviewing") if status == "active" else (status,)
    q = ",".join("?" * len(statuses))
    total = dbm.scalar(conn, f"SELECT COUNT(*) FROM reports WHERE status IN ({q})", statuses)
    rows = dbm.all_(conn, f"""
        SELECT r.id, r.category, r.status, r.created_at, r.reported_user_id, r.message_id,
          tp.display_name AS reported_name, rp.display_name AS reporter_name,
          (SELECT COUNT(*) FROM reports r2 WHERE r2.reported_user_id=r.reported_user_id) AS target_report_count
        FROM reports r LEFT JOIN profiles tp ON tp.user_id=r.reported_user_id
        LEFT JOIN profiles rp ON rp.user_id=r.reporter_id
        WHERE r.status IN ({q})
        ORDER BY r.category='underage' DESC, r.created_at ASC LIMIT ? OFFSET ?""", (*statuses, limit, offset))
    return {"reports": [{**dict(r), "category_label": REPORT_CATEGORIES.get(r["category"], r["category"])} for r in rows],
            "total": total, "page": page, "pages": max(1, -(-total // limit))}


def report_detail(conn: sqlite3.Connection, actor: CurrentUser, report_id: str) -> dict:
    r = dbm.one(conn, "SELECT * FROM reports WHERE id=?", (report_id,))
    if not r:
        raise not_found("Report not found.")
    evidence = json.loads(r["evidence_snapshot"]) if r["evidence_snapshot"] else None
    if evidence:
        evidence["attachment_urls"] = [storage.signed_url(k, ttl=300) for k in evidence.get("attachments", [])]
        evidence.pop("attachments", None)
    audit.log(conn, actor.id, "admin.report_viewed", "report", report_id)
    return {"report": {**dict(r), "category_label": REPORT_CATEGORIES.get(r["category"], r["category"]),
                       "evidence_snapshot": None},
            "evidence": evidence,
            "reported": user_detail(conn, r["reported_user_id"])}


def resolve_report(conn: sqlite3.Connection, actor: CurrentUser, report_id: str, action: str, note: str | None) -> None:
    if action not in ("dismiss", "warn", "suspend", "reviewing"):
        raise bad_request("Unknown action.")
    r = dbm.one(conn, "SELECT * FROM reports WHERE id=?", (report_id,))
    if not r:
        raise not_found("Report not found.")
    if action == "suspend":
        suspend(conn, actor, r["reported_user_id"], note or "Violation of community guidelines", report_id)
    elif action == "warn":
        warn(conn, actor, r["reported_user_id"], note or "Guideline reminder", report_id)
    with dbm.tx(conn):
        if action == "reviewing":
            conn.execute("UPDATE reports SET status='reviewing', assigned_to=? WHERE id=?", (actor.id, report_id))
        else:
            conn.execute("UPDATE reports SET status=?, resolution=?, resolved_at=?, assigned_to=? WHERE id=?",
                         ("dismissed" if action == "dismiss" else "resolved", (note or action)[:1000], dbm.iso(),
                          actor.id, report_id))
            if r["reporter_id"]:
                notify(conn, r["reporter_id"], "moderation", "Update on your report",
                       "Thanks for helping keep the community safe. We've reviewed your report.", "/safety",
                       dedupe_key=f"report:{report_id}")
        audit.log(conn, actor.id, "admin.report_" + action, "report", report_id, {"note": note})


def list_verifications(conn: sqlite3.Connection) -> list[dict]:
    rows = dbm.all_(conn, "SELECT v.id, v.user_id, v.storage_key, v.created_at, p.display_name FROM photo_verifications v"
                          " LEFT JOIN profiles p ON p.user_id=v.user_id WHERE v.status='pending' ORDER BY v.created_at LIMIT 50")
    out = []
    for r in rows:
        photos = [storage.signed_url(x[0], ttl=300) for x in dbm.all_(
            conn, "SELECT storage_key FROM profile_photos WHERE user_id=? ORDER BY position LIMIT 3", (r["user_id"],))]
        out.append({"id": r["id"], "user_id": r["user_id"], "display_name": r["display_name"], "created_at": r["created_at"],
                    "selfie_url": storage.signed_url(r["storage_key"], ttl=300), "profile_photos": photos})
    return out


def review_verification(conn: sqlite3.Connection, actor: CurrentUser, verification_id: str, approve: bool,
                        notes: str | None) -> None:
    with dbm.tx(conn):
        v = dbm.one(conn, "SELECT * FROM photo_verifications WHERE id=? AND status='pending'", (verification_id,))
        if not v:
            raise not_found("Verification request not found.")
        conn.execute("UPDATE photo_verifications SET status=?, reviewer_id=?, reviewed_at=?, notes=? WHERE id=?",
                     ("approved" if approve else "rejected", actor.id, dbm.iso(), notes, verification_id))
        if approve:
            conn.execute("UPDATE profiles SET photo_verified=1 WHERE user_id=?", (v["user_id"],))
        _mod_action(conn, actor, v["user_id"], "photo_verification_" + ("approved" if approve else "rejected"), notes)
        notify(conn, v["user_id"], "verification",
               "Your photo verification was approved" if approve else "Your photo verification wasn't approved",
               "A verified badge now shows on your profile." if approve else
               "Please try again with a clear, well-lit selfie matching the requested pose.",
               "/profile", dedupe_key=f"verif:{verification_id}")


def list_appeals(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in dbm.all_(
        conn, "SELECT a.id, a.user_id, a.message, a.created_at, u.email, u.suspended_reason, p.display_name FROM appeals a"
              " JOIN users u ON u.id=a.user_id LEFT JOIN profiles p ON p.user_id=a.user_id"
              " WHERE a.status='open' ORDER BY a.created_at")]


def decide_appeal(conn: sqlite3.Connection, actor: CurrentUser, appeal_id: str, overturn: bool, decision: str) -> None:
    a = dbm.one(conn, "SELECT * FROM appeals WHERE id=? AND status='open'", (appeal_id,))
    if not a:
        raise not_found("Appeal not found.")
    if overturn:
        reinstate(conn, actor, a["user_id"], f"Appeal overturned: {decision}")
    with dbm.tx(conn):
        conn.execute("UPDATE appeals SET status=?, reviewer_id=?, decision=?, reviewed_at=? WHERE id=?",
                     ("overturned" if overturn else "upheld", actor.id, (decision or "")[:1000], dbm.iso(), appeal_id))
        audit.log(conn, actor.id, "admin.appeal_" + ("overturned" if overturn else "upheld"), "appeal", appeal_id)
        if not overturn:
            notify(conn, a["user_id"], "moderation", "Your appeal was reviewed",
                   "After review, the suspension remains in place.", "/account/suspended", email=True)


def list_subscriptions(conn: sqlite3.Connection, page: int) -> dict:
    limit, offset = _page(page)
    total = dbm.scalar(conn, "SELECT COUNT(*) FROM subscriptions")
    rows = dbm.all_(conn, "SELECT s.*, u.email FROM subscriptions s JOIN users u ON u.id=s.user_id"
                          " ORDER BY s.created_at DESC LIMIT ? OFFSET ?", (limit, offset))
    return {"subscriptions": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, -(-total // limit))}


def grant_plan(conn: sqlite3.Connection, actor: CurrentUser, target_id: str, plan: str, days: int) -> None:
    if not actor.is_admin:
        raise forbidden()
    if plan not in get_settings().plans or plan == "free" or not (1 <= days <= 366):
        raise bad_request("Choose a paid plan and 1-366 days.")
    with dbm.tx(conn):
        if not dbm.one(conn, "SELECT 1 FROM users WHERE id=? AND status='active'", (target_id,)):
            raise not_found("User not found.")
        conn.execute("UPDATE subscriptions SET status='expired', updated_at=? WHERE user_id=? AND status IN ('active','canceled','past_due')",
                     (dbm.iso(), target_id))
        sid = dbm.new_id()
        conn.execute("INSERT INTO subscriptions (id, user_id, plan, status, provider, provider_subscription_id,"
                     " current_period_end, cancel_at_period_end, created_at, updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)",
                     (sid, target_id, plan, "canceled", "complimentary", f"comp-{sid}", dbm.iso_in(days=days),
                      dbm.iso(), dbm.iso()))
        _mod_action(conn, actor, target_id, "grant_plan", f"{plan} for {days} days")


def audit_logs(conn: sqlite3.Connection, page: int, action: str | None) -> dict:
    limit, offset = _page(page)
    where, params = "1=1", []
    if action:
        where = "a.action LIKE ?"
        params.append(f"{action}%")
    total = dbm.scalar(conn, f"SELECT COUNT(*) FROM audit_logs a WHERE {where}", tuple(params))
    rows = dbm.all_(conn, f"SELECT a.*, p.display_name AS actor_name, u.email AS actor_email FROM audit_logs a"
                          f" LEFT JOIN profiles p ON p.user_id=a.actor_id LEFT JOIN users u ON u.id=a.actor_id"
                          f" WHERE {where} ORDER BY a.id DESC LIMIT ? OFFSET ?", (*params, limit, offset))
    return {"logs": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, -(-total // limit))}


def save_content(conn: sqlite3.Connection, actor: CurrentUser, slug: str, title: str, body: str) -> None:
    if slug not in CONTENT_SLUGS:
        raise bad_request("Unknown page.")
    if not title.strip() or not body.strip():
        raise bad_request("Title and body are required.")
    with dbm.tx(conn):
        conn.execute("INSERT INTO content_pages (slug, title, body, updated_at, updated_by) VALUES (?,?,?,?,?)"
                     " ON CONFLICT(slug) DO UPDATE SET title=excluded.title, body=excluded.body,"
                     " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                     (slug, title.strip()[:120], body[:20000], dbm.iso(), actor.id))
        audit.log(conn, actor.id, "admin.content_updated", "content", slug)


CONTENT_SLUGS = ("faq", "safety-tips")


def get_content(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = dbm.one(conn, "SELECT * FROM content_pages WHERE slug=?", (slug,))
    return dict(row) if row else None
