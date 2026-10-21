"""Accounts: registration, sessions, email verification, password reset,
phone OTP verification, deactivation and deletion."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date

from .. import db as dbm
from .. import security
from ..config import get_settings
from ..providers.messaging import DeliveryError, send_email, send_sms
from ..providers import storage
from ..web import APIError, CurrentUser, bad_request, too_many
from . import audit

log = logging.getLogger("heartsync.accounts")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email)) and len(email) <= 254


def normalize_phone(raw: str, default_cc: str = "+91") -> str | None:
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = digits.lstrip("0")
        if len(digits) == 10:
            digits = default_cc + digits
        else:
            return None
    return digits if E164_RE.match(digits) else None


def mask_phone(e164: str) -> str:
    return e164[:3] + " " + "•" * max(0, len(e164) - 7) + e164[-4:]


def age_on(dob: str, today: date | None = None) -> int:
    d = date.fromisoformat(dob)
    t = today or dbm.utcnow().date()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(conn: sqlite3.Connection, email: str, password: str, dob: str, ip_hash: str | None) -> str:
    s = get_settings()
    email = normalize_email(email)
    fields: dict[str, str] = {}
    if not valid_email(email):
        fields["email"] = "Enter a valid email address."
    problems = security.password_problems(password, email)
    if problems:
        fields["password"] = " ".join(problems)
    try:
        dob_date = date.fromisoformat(dob)
        if age_on(dob) < s.min_age:
            fields["date_of_birth"] = f"You must be at least {s.min_age} to use {s.brand_name}."
        elif age_on(dob) > 100 or dob_date > dbm.utcnow().date():
            fields["date_of_birth"] = "Enter a valid date of birth."
    except ValueError:
        fields["date_of_birth"] = "Enter a valid date of birth."
    if fields:
        raise bad_request("Please check the highlighted fields.", fields, code="validation_error")

    with dbm.tx(conn):
        if dbm.one(conn, "SELECT 1 FROM users WHERE email=?", (email,)):
            # Do not reveal account existence in detail; generic but actionable.
            raise APIError(409, "email_taken", "An account with this email may already exist. Try signing in or resetting your password.")
        uid = dbm.new_id()
        now = dbm.iso()
        conn.execute(
            "INSERT INTO users (id, email, password_hash, date_of_birth, terms_accepted_at, created_at, updated_at, last_active_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (uid, email, security.hash_password(password), dob, now, now, now, now),
        )
        conn.execute("INSERT INTO user_contacts (user_id, updated_at) VALUES (?,?)", (uid, now))
        conn.execute("INSERT INTO dating_preferences (user_id, updated_at) VALUES (?,?)", (uid, now))
        audit.log(conn, uid, "account.register", "user", uid, ip_hash=ip_hash)
        send_verification_email(conn, uid, email)
    return uid


def _issue_token(conn: sqlite3.Connection, user_id: str, purpose: str, hours: float) -> str:
    token = security.new_token()
    conn.execute(
        "UPDATE auth_tokens SET used_at=? WHERE user_id=? AND purpose=? AND used_at IS NULL",
        (dbm.iso(), user_id, purpose),
    )
    conn.execute(
        "INSERT INTO auth_tokens (id, user_id, purpose, token_hash, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (dbm.new_id(), user_id, purpose, security.token_hash(token), dbm.iso(), dbm.iso_in(hours=hours)),
    )
    return token


def _consume_token(conn: sqlite3.Connection, token: str, purpose: str) -> str | None:
    row = dbm.one(
        conn,
        "SELECT id, user_id, expires_at FROM auth_tokens WHERE token_hash=? AND purpose=? AND used_at IS NULL",
        (security.token_hash(token or ""), purpose),
    )
    if not row or row["expires_at"] < dbm.iso():
        return None
    conn.execute("UPDATE auth_tokens SET used_at=? WHERE id=?", (dbm.iso(), row["id"]))
    return row["user_id"]


def send_verification_email(conn: sqlite3.Connection, user_id: str, email: str) -> None:
    s = get_settings()
    token = _issue_token(conn, user_id, "verify_email", hours=48)
    link = f"{s.base_url}/verify-email/confirm?token={token}"
    try:
        send_email(email, f"Confirm your {s.brand_name} email",
                   f"Welcome to {s.brand_name}!\n\nConfirm your email address:\n{link}\n\n"
                   "This link expires in 48 hours. If you didn't sign up, ignore this email.", conn=conn)
    except DeliveryError as exc:
        log.error("verification email failed: %s", exc)


def confirm_email(conn: sqlite3.Connection, token: str) -> str | None:
    with dbm.tx(conn):
        uid = _consume_token(conn, token, "verify_email")
        if uid:
            conn.execute(
                "UPDATE users SET email_verified_at=COALESCE(email_verified_at, ?), updated_at=? WHERE id=?",
                (dbm.iso(), dbm.iso(), uid),
            )
            audit.log(conn, uid, "account.email_verified", "user", uid)
    return uid


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def authenticate(conn: sqlite3.Connection, email: str, password: str) -> str | None:
    row = dbm.one(conn, "SELECT id, password_hash, status FROM users WHERE email=?", (normalize_email(email),))
    if not row or row["status"] == "deleted":
        security.dummy_verify(password)
        return None
    if not security.verify_password(password, row["password_hash"]):
        return None
    return row["id"]


def create_session(conn: sqlite3.Connection, user_id: str, user_agent: str | None, ip_hash: str | None) -> tuple[str, str]:
    s = get_settings()
    token = security.new_token()
    csrf = security.new_token(24)
    now = dbm.iso()
    with dbm.tx(conn):
        conn.execute(
            "INSERT INTO sessions (id, user_id, token_hash, csrf_token, user_agent, ip_hash, created_at, last_seen_at, expires_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (dbm.new_id(), user_id, security.token_hash(token), csrf, (user_agent or "")[:200], ip_hash, now, now,
             dbm.iso_in(days=s.session_days)),
        )
        # Reactivate a self-deactivated account on sign-in.
        conn.execute(
            "UPDATE users SET status='active', last_active_at=?, updated_at=? WHERE id=? AND status='deactivated'",
            (now, now, user_id),
        )
        audit.log(conn, user_id, "auth.login", "user", user_id, ip_hash=ip_hash)
    return token, csrf


def load_session(conn: sqlite3.Connection, token: str | None) -> CurrentUser | None:
    if not token:
        return None
    row = dbm.one(
        conn,
        "SELECT s.id AS sid, s.csrf_token, s.expires_at, s.last_seen_at, u.id, u.email, u.role, u.status,"
        " u.email_verified_at, u.date_of_birth, u.last_active_at, c.phone_verified_at, p.onboarded_at, p.display_name,"
        " (SELECT 1 FROM user_mfa m WHERE m.user_id=u.id AND m.enabled_at IS NOT NULL) AS mfa_on"
        " FROM sessions s JOIN users u ON u.id=s.user_id"
        " LEFT JOIN user_contacts c ON c.user_id=u.id LEFT JOIN profiles p ON p.user_id=u.id"
        " WHERE s.token_hash=? AND s.revoked_at IS NULL",
        (security.token_hash(token),),
    )
    if not row or row["expires_at"] < dbm.iso() or row["status"] == "deleted":
        return None
    now = dbm.iso()
    # Throttle activity writes to once a minute.
    if (row["last_seen_at"] or "") < dbm.iso_in(minutes=-1):
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (now, row["sid"]))
        conn.execute("UPDATE users SET last_active_at=? WHERE id=?", (now, row["id"]))
    return CurrentUser(
        id=row["id"], email=row["email"], role=row["role"], status=row["status"],
        email_verified=bool(row["email_verified_at"]), phone_verified=bool(row["phone_verified_at"]),
        onboarded=bool(row["onboarded_at"]), display_name=row["display_name"], session_id=row["sid"],
        csrf_token=row["csrf_token"], date_of_birth=row["date_of_birth"], mfa_enabled=bool(row["mfa_on"]),
    )


def revoke_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (dbm.iso(), session_id))


def revoke_other_sessions(conn: sqlite3.Connection, user_id: str, keep_session_id: str | None) -> int:
    cur = conn.execute(
        "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND id IS NOT ?",
        (dbm.iso(), user_id, keep_session_id),
    )
    return cur.rowcount


def list_sessions(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = dbm.all_(
        conn,
        "SELECT id, user_agent, created_at, last_seen_at FROM sessions WHERE user_id=? AND revoked_at IS NULL"
        " AND expires_at > ? ORDER BY last_seen_at DESC",
        (user_id, dbm.iso()),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Password reset / change
# ---------------------------------------------------------------------------
def request_password_reset(conn: sqlite3.Connection, email: str) -> None:
    """Always behaves the same whether or not the account exists."""
    s = get_settings()
    row = dbm.one(conn, "SELECT id, email FROM users WHERE email=? AND status!='deleted'", (normalize_email(email),))
    if not row:
        security.dummy_verify("x")
        return
    with dbm.tx(conn):
        token = _issue_token(conn, row["id"], "reset_password", hours=1)
        audit.log(conn, row["id"], "auth.password_reset_requested", "user", row["id"])
        try:
            send_email(row["email"], f"Reset your {s.brand_name} password",
                       f"Use this link to choose a new password (valid for 1 hour):\n"
                       f"{s.base_url}/reset-password?token={token}\n\n"
                       "If you didn't ask for this, you can ignore this email.", conn=conn)
        except DeliveryError as exc:
            log.error("reset email failed: %s", exc)


def reset_password(conn: sqlite3.Connection, token: str, new_password: str) -> str:
    with dbm.tx(conn):
        row = dbm.one(conn, "SELECT u.email FROM auth_tokens t JOIN users u ON u.id=t.user_id"
                            " WHERE t.token_hash=? AND t.purpose='reset_password'", (security.token_hash(token or ""),))
        problems = security.password_problems(new_password, row["email"] if row else "")
        if problems:
            raise bad_request("Please choose a stronger password.", {"password": " ".join(problems)}, code="validation_error")
        uid = _consume_token(conn, token, "reset_password")
        if not uid:
            raise bad_request("This reset link is invalid or has expired. Request a new one.", code="invalid_token")
        conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                     (security.hash_password(new_password), dbm.iso(), uid))
        revoke_other_sessions(conn, uid, None)  # sign out everywhere
        audit.log(conn, uid, "auth.password_reset", "user", uid)
    return uid


def change_password(conn: sqlite3.Connection, user: CurrentUser, current: str, new: str) -> None:
    row = dbm.one(conn, "SELECT password_hash FROM users WHERE id=?", (user.id,))
    if not row or not security.verify_password(current, row["password_hash"]):
        raise bad_request("Your current password is incorrect.", {"current_password": "Incorrect password."})
    problems = security.password_problems(new, user.email)
    if problems:
        raise bad_request("Please choose a stronger password.", {"new_password": " ".join(problems)}, code="validation_error")
    with dbm.tx(conn):
        conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                     (security.hash_password(new), dbm.iso(), user.id))
        revoke_other_sessions(conn, user.id, user.session_id)
        audit.log(conn, user.id, "auth.password_changed", "user", user.id)


# ---------------------------------------------------------------------------
# Phone OTP
# ---------------------------------------------------------------------------
def send_phone_otp(conn: sqlite3.Connection, user: CurrentUser, raw_phone: str, ip: str) -> dict:
    s = get_settings()
    phone = normalize_phone(raw_phone)
    if not phone:
        raise bad_request("Enter a valid mobile number with country code, e.g. +91 98765 43210.",
                          {"phone": "Invalid phone number."})
    if not security.allow("otp_send", user.id) or not security.allow("otp_send", "ip", ip) \
            or not security.allow("otp_send_phone", phone):
        raise too_many("Too many codes requested. Please wait before trying again.")
    owner = dbm.one(conn, "SELECT user_id FROM user_contacts WHERE phone_e164=? AND phone_verified_at IS NOT NULL", (phone,))
    with dbm.tx(conn):
        # Invalidate previous outstanding codes.
        conn.execute("UPDATE otp_codes SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL", (dbm.iso(), user.id))
        if owner and owner["user_id"] != user.id:
            # Never reveal to the requester that this number is registered. Tell the
            # number's owner instead; no usable code is issued.
            try:
                send_sms(phone, f"{s.brand_name}: someone tried to add this number to another account. "
                                "If this was you, sign in to your existing account instead.", conn=conn)
            except DeliveryError:
                pass
            audit.log(conn, user.id, "phone.otp_conflict", "user", user.id)
        else:
            code = security.numeric_code(6)
            conn.execute(
                "INSERT INTO otp_codes (id, user_id, phone_e164, code_hash, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                (dbm.new_id(), user.id, phone, security.token_hash(f"{user.id}:{phone}:{code}"), dbm.iso(),
                 dbm.iso_in(minutes=OTP_TTL_MIN)),
            )
            try:
                send_sms(phone, f"{code} is your {s.brand_name} verification code. It expires in "
                                f"{OTP_TTL_MIN} minutes. Never share it with anyone.", conn=conn)
            except DeliveryError as exc:
                raise APIError(503, "sms_unavailable", str(exc))
            audit.log(conn, user.id, "phone.otp_sent", "user", user.id)
    return {"masked": mask_phone(phone), "expires_in_minutes": OTP_TTL_MIN}


def verify_phone_otp(conn: sqlite3.Connection, user: CurrentUser, code: str, ip: str) -> None:
    if not security.allow("otp_verify", user.id) or not security.allow("otp_verify", "ip", ip):
        raise too_many("Too many attempts. Please wait and request a new code.")
    code = (code or "").strip()
    with dbm.tx(conn):
        row = dbm.one(
            conn,
            "SELECT * FROM otp_codes WHERE user_id=? AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (user.id,),
        )
        generic = bad_request("That code is invalid or has expired.", {"code": "Invalid or expired code."},
                              code="invalid_otp")
        if not row or row["expires_at"] < dbm.iso():
            raise generic
        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            conn.execute("UPDATE otp_codes SET consumed_at=? WHERE id=?", (dbm.iso(), row["id"]))
            raise generic
        expected = security.token_hash(f"{user.id}:{row['phone_e164']}:{code}")
        import hmac as _hmac

        if not (code.isdigit() and len(code) == 6 and _hmac.compare_digest(expected, row["code_hash"])):
            conn.execute("UPDATE otp_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
            # Commit the attempt counter even though we raise.
            conn.execute("COMMIT")
            conn.execute("BEGIN IMMEDIATE")
            raise generic
        taken = dbm.one(conn, "SELECT user_id FROM user_contacts WHERE phone_e164=? AND user_id!=?",
                        (row["phone_e164"], user.id))
        if taken:
            raise generic
        conn.execute("UPDATE otp_codes SET consumed_at=? WHERE id=?", (dbm.iso(), row["id"]))
        conn.execute(
            "UPDATE user_contacts SET phone_e164=?, phone_verified_at=?, updated_at=? WHERE user_id=?",
            (row["phone_e164"], dbm.iso(), dbm.iso(), user.id),
        )
        # Changing number revokes any earlier shares of the old number.
        conn.execute("UPDATE phone_shares SET revoked_at=? WHERE owner_id=? AND revoked_at IS NULL", (dbm.iso(), user.id))
        audit.log(conn, user.id, "phone.verified", "user", user.id)


# ---------------------------------------------------------------------------
# Deactivation & deletion
# ---------------------------------------------------------------------------
def deactivate(conn: sqlite3.Connection, user: CurrentUser) -> None:
    with dbm.tx(conn):
        conn.execute("UPDATE users SET status='deactivated', updated_at=? WHERE id=? AND status='active'",
                     (dbm.iso(), user.id))
        audit.log(conn, user.id, "account.deactivated", "user", user.id)


def delete_account(conn: sqlite3.Connection, user: CurrentUser, password: str) -> None:
    """Hard-deletes personal data. Keeps a tombstone user row (no PII) so that
    moderation records and the counterpart's conversation integrity survive,
    per the data-retention policy (docs/DATABASE.md#data-retention)."""
    row = dbm.one(conn, "SELECT password_hash FROM users WHERE id=?", (user.id,))
    if not row or not security.verify_password(password, row["password_hash"]):
        raise bad_request("Password is incorrect.", {"password": "Incorrect password."})
    keys = [r["storage_key"] for r in dbm.all_(conn, "SELECT storage_key FROM profile_photos WHERE user_id=?", (user.id,))]
    keys += [r["storage_key"] for r in dbm.all_(conn, "SELECT storage_key FROM photo_verifications WHERE user_id=?", (user.id,))]
    keys += [r["storage_key"] for r in dbm.all_(
        conn, "SELECT a.storage_key FROM message_attachments a JOIN messages m ON m.id=a.message_id WHERE m.sender_id=?",
        (user.id,))]
    now = dbm.iso()
    with dbm.tx(conn):
        conn.execute("DELETE FROM profiles WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM profile_photos WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM photo_verifications WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM profile_interests WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM swipes WHERE from_user=? OR to_user=?", (user.id, user.id))
        conn.execute("DELETE FROM saved_profiles WHERE user_id=? OR saved_user_id=?", (user.id, user.id))
        conn.execute("DELETE FROM filter_presets WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM profile_views WHERE viewer_id=? OR viewed_id=?", (user.id, user.id))
        conn.execute("DELETE FROM notifications WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM otp_codes WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM auth_tokens WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM user_mfa WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM mfa_challenges WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM dating_preferences WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM phone_shares WHERE owner_id=? OR recipient_id=?", (user.id, user.id))
        conn.execute("UPDATE user_contacts SET phone_e164=NULL, phone_verified_at=NULL, updated_at=? WHERE user_id=?",
                     (now, user.id))
        conn.execute(
            "DELETE FROM message_attachments WHERE message_id IN (SELECT id FROM messages WHERE sender_id=?)", (user.id,))
        conn.execute("UPDATE messages SET body=NULL, deleted_at=COALESCE(deleted_at, ?) WHERE sender_id=?", (now, user.id))
        conn.execute("UPDATE matches SET unmatched_at=COALESCE(unmatched_at, ?), unmatched_by=COALESCE(unmatched_by, ?)"
                     " WHERE user_a=? OR user_b=?", (now, user.id, user.id, user.id))
        conn.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user.id))
        conn.execute(
            "UPDATE users SET email=?, password_hash='!', status='deleted', deleted_at=?, updated_at=?,"
            " date_of_birth='1900-01-01' WHERE id=?",
            (f"deleted-{user.id}@invalid", now, now, user.id),
        )
        audit.log(conn, user.id, "account.deleted", "user", user.id)
    for key in keys:
        storage.delete(key)
