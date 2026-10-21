"""Two-factor sign-in with an authenticator app (TOTP, RFC 6238).

* The shared secret is encrypted at rest with Fernet. The key comes from
  MFA_ENCRYPTION_KEY when set, otherwise it is derived from SECRET_KEY
  (rotating SECRET_KEY without MFA_ENCRYPTION_KEY invalidates enrolments).
* A code is accepted for the current 30-second step +/- one step of clock
  drift, and never twice: the last accepted step is stored per user.
* Ten single-use recovery codes are shown once at enrolment and stored only
  as keyed hashes.
* Sign-in with 2FA is two steps. The password step creates a short-lived
  challenge (5 minutes, 5 attempts); a session is only created once the
  second factor succeeds.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import struct
import time
import urllib.parse

from cryptography.fernet import Fernet, InvalidToken

from .. import db as dbm
from .. import security
from ..config import env, get_settings
from ..web import APIError, CurrentUser, bad_request
from . import audit

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
CHALLENGE_MINUTES = 5
CHALLENGE_ATTEMPTS = 5
RECOVERY_CODES = 10


# ----------------------------------------------------------------- crypto
def _fernet() -> Fernet:
    explicit = env("MFA_ENCRYPTION_KEY")
    if explicit:
        return Fernet(explicit.encode())
    raw = hashlib.sha256(b"heartsync-mfa-v1:" + get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt(secret_b32: str) -> str:
    return _fernet().encrypt(secret_b32.encode()).decode()


def _decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise APIError(500, "mfa_key_mismatch", "Two-factor settings can't be read. Contact support.")


# ------------------------------------------------------------------- TOTP
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _b32decode(secret: str) -> bytes:
    s = secret.upper().replace(" ", "")
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP_SECONDS)


def matching_step(secret: str, code: str, after_step: int, now: float | None = None) -> int | None:
    """Return the step a code belongs to, if valid and newer than after_step."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    base = current_step(now)
    for step in range(base - DRIFT_STEPS, base + DRIFT_STEPS + 1):
        if step > after_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, email: str) -> str:
    issuer = get_settings().brand_name
    label = urllib.parse.quote(f"{issuer}:{email}")
    q = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{q}"


# ---------------------------------------------------------------- recovery
def _recovery_hash(code: str) -> str:
    return security.sign("mfa-recovery:" + code.strip().lower().replace("-", "").replace(" ", ""))


def _new_recovery_code() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes
    raw = "".join(secrets.choice(alphabet) for _ in range(10))
    return raw[:5] + "-" + raw[5:]


# ------------------------------------------------------------------ state
def status(conn: sqlite3.Connection, user_id: str) -> dict:
    row = dbm.one(conn, "SELECT enabled_at FROM user_mfa WHERE user_id=?", (user_id,))
    remaining = dbm.scalar(conn, "SELECT COUNT(*) FROM mfa_recovery_codes WHERE user_id=? AND used_at IS NULL",
                           (user_id,))
    return {"enabled": bool(row and row["enabled_at"]), "enabled_at": row["enabled_at"] if row else None,
            "recovery_codes_remaining": remaining or 0}


def is_enabled(conn: sqlite3.Connection, user_id: str) -> bool:
    return bool(dbm.scalar(conn, "SELECT 1 FROM user_mfa WHERE user_id=? AND enabled_at IS NOT NULL", (user_id,)))


def _check_password(conn: sqlite3.Connection, user_id: str, password: str) -> None:
    ph = dbm.scalar(conn, "SELECT password_hash FROM users WHERE id=?", (user_id,))
    if not ph or not security.verify_password(password or "", ph):
        raise bad_request("Your password is incorrect.", {"password": "Incorrect password."}, code="invalid_password")


def begin_setup(conn: sqlite3.Connection, user: CurrentUser, password: str) -> dict:
    _check_password(conn, user.id, password)
    if is_enabled(conn, user.id):
        raise bad_request("Two-factor sign-in is already on.", code="mfa_already_enabled")
    secret = new_secret()
    now = dbm.iso()
    with dbm.tx(conn):
        conn.execute(
            "INSERT INTO user_mfa (user_id, secret_enc, enabled_at, last_used_step, created_at, updated_at)"
            " VALUES (?,?,NULL,0,?,?) ON CONFLICT(user_id) DO UPDATE SET secret_enc=excluded.secret_enc,"
            " enabled_at=NULL, last_used_step=0, updated_at=excluded.updated_at",
            (user.id, _encrypt(secret), now, now),
        )
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    return {"secret": grouped, "otpauth_uri": otpauth_uri(secret, user.email)}


def confirm_setup(conn: sqlite3.Connection, user: CurrentUser, code: str) -> list[str]:
    row = dbm.one(conn, "SELECT * FROM user_mfa WHERE user_id=?", (user.id,))
    if not row or row["enabled_at"]:
        raise bad_request("Start two-factor setup first.", code="mfa_not_pending")
    step = matching_step(_decrypt(row["secret_enc"]), code, row["last_used_step"])
    if step is None:
        raise bad_request("That code didn't work. Check your app's time and try the newest code.",
                          {"code": "Invalid code."}, code="invalid_code")
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
    now = dbm.iso()
    with dbm.tx(conn):
        conn.execute("UPDATE user_mfa SET enabled_at=?, last_used_step=?, updated_at=? WHERE user_id=?",
                     (now, step, now, user.id))
        conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id=?", (user.id,))
        conn.executemany("INSERT INTO mfa_recovery_codes (id, user_id, code_hash, created_at) VALUES (?,?,?,?)",
                         [(dbm.new_id(), user.id, _recovery_hash(c), now) for c in codes])
        audit.log(conn, user.id, "auth.mfa_enabled", "user", user.id)
    return codes


def disable(conn: sqlite3.Connection, user: CurrentUser, password: str, code: str) -> None:
    _check_password(conn, user.id, password)
    if not is_enabled(conn, user.id):
        raise bad_request("Two-factor sign-in is not on.", code="mfa_not_enabled")
    if not _verify_second_factor(conn, user.id, code):
        raise bad_request("That code didn't work.", {"code": "Invalid code."}, code="invalid_code")
    with dbm.tx(conn):
        conn.execute("DELETE FROM user_mfa WHERE user_id=?", (user.id,))
        conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id=?", (user.id,))
        audit.log(conn, user.id, "auth.mfa_disabled", "user", user.id)


def regenerate_recovery_codes(conn: sqlite3.Connection, user: CurrentUser, password: str) -> list[str]:
    _check_password(conn, user.id, password)
    if not is_enabled(conn, user.id):
        raise bad_request("Two-factor sign-in is not on.", code="mfa_not_enabled")
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
    now = dbm.iso()
    with dbm.tx(conn):
        conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id=?", (user.id,))
        conn.executemany("INSERT INTO mfa_recovery_codes (id, user_id, code_hash, created_at) VALUES (?,?,?,?)",
                         [(dbm.new_id(), user.id, _recovery_hash(c), now) for c in codes])
        audit.log(conn, user.id, "auth.mfa_recovery_regenerated", "user", user.id)
    return codes


def _verify_second_factor(conn: sqlite3.Connection, user_id: str, code: str) -> str | None:
    """Accept a TOTP code or an unused recovery code. Returns which was used."""
    code = (code or "").strip()
    row = dbm.one(conn, "SELECT * FROM user_mfa WHERE user_id=? AND enabled_at IS NOT NULL", (user_id,))
    if not row:
        return None
    digits = code.replace(" ", "")
    if digits.isdigit():
        step = matching_step(_decrypt(row["secret_enc"]), digits, row["last_used_step"])
        if step is None:
            return None
        # Conditional update makes concurrent reuse of the same code fail.
        cur = conn.execute("UPDATE user_mfa SET last_used_step=?, updated_at=? WHERE user_id=? AND last_used_step < ?",
                           (step, dbm.iso(), user_id, step))
        return "totp" if cur.rowcount == 1 else None
    if len(code) > 20:
        return None
    cur = conn.execute("UPDATE mfa_recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? AND used_at IS NULL",
                       (dbm.iso(), user_id, _recovery_hash(code)))
    return "recovery" if cur.rowcount == 1 else None


# --------------------------------------------------------------- sign-in
def start_challenge(conn: sqlite3.Connection, user_id: str) -> str:
    token = security.new_token()
    now = dbm.iso()
    with dbm.tx(conn):
        conn.execute("DELETE FROM mfa_challenges WHERE user_id=? AND (used_at IS NOT NULL OR expires_at < ?)",
                     (user_id, now))
        conn.execute("INSERT INTO mfa_challenges (id, user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?,?)",
                     (dbm.new_id(), user_id, security.token_hash(token), now, dbm.iso_in(minutes=CHALLENGE_MINUTES)))
    return token


def complete_challenge(conn: sqlite3.Connection, token: str | None, code: str) -> tuple[str, str]:
    """Return (user_id, method) or raise. Burns the challenge after too many tries."""
    expired = APIError(401, "mfa_challenge_expired", "Your sign-in timed out. Enter your password again.")
    if not token:
        raise expired
    with dbm.tx(conn):
        ch = dbm.one(conn, "SELECT * FROM mfa_challenges WHERE token_hash=?", (security.token_hash(token),))
        if not ch or ch["used_at"] or ch["expires_at"] < dbm.iso() or ch["attempts"] >= CHALLENGE_ATTEMPTS:
            raise expired
        conn.execute("UPDATE mfa_challenges SET attempts=attempts+1 WHERE id=?", (ch["id"],))
        method = _verify_second_factor(conn, ch["user_id"], code)
        if not method:
            left = CHALLENGE_ATTEMPTS - ch["attempts"] - 1
            if left <= 0:
                conn.execute("UPDATE mfa_challenges SET used_at=? WHERE id=?", (dbm.iso(), ch["id"]))
                audit.log(conn, ch["user_id"], "auth.mfa_challenge_burned", "user", ch["user_id"])
                # Commit the burn before signalling failure.
                err = expired
            else:
                err = bad_request(f"That code didn't work. {left} attempt{'s' if left != 1 else ''} left.",
                                  {"code": "Invalid code."}, code="invalid_code")
        else:
            conn.execute("UPDATE mfa_challenges SET used_at=? WHERE id=?", (dbm.iso(), ch["id"]))
            audit.log(conn, ch["user_id"], "auth.mfa_passed", "user", ch["user_id"], {"method": method})
            err = None
    if err:
        raise err
    return ch["user_id"], method
