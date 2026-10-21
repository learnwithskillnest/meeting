"""Security primitives: password hashing, tokens, signing, rate limiting."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from .config import get_settings

# ---------------------------------------------------------------------------
# Passwords (scrypt, memory-hard, stdlib only)
# ---------------------------------------------------------------------------
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:
        return False


# A pre-computed hash used to equalise timing when the account does not exist.
_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("timing-equaliser")
    verify_password(password, _DUMMY_HASH)


COMMON_PASSWORDS = {
    "password", "password1", "12345678", "123456789", "qwerty123", "iloveyou", "11111111",
    "abc12345", "letmein1", "welcome1", "password123", "admin123", "heartsync",
}


def password_problems(password: str, email: str = "") -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("Use at least 10 characters.")
    if len(password) > 200:
        problems.append("Password is too long.")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("That password is too common.")
    if email and password.lower() == email.lower():
        problems.append("Password must differ from your email.")
    classes = sum(
        [any(c.islower() for c in password), any(c.isupper() for c in password),
         any(c.isdigit() for c in password), any(not c.isalnum() for c in password)]
    )
    if classes < 2:
        problems.append("Mix letters with numbers or symbols.")
    return problems


# ---------------------------------------------------------------------------
# Tokens and signing
# ---------------------------------------------------------------------------
def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """Hash for storing bearer tokens; keyed so a DB leak alone is not enough."""
    key = get_settings().secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def sign(value: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:40]


def verify_signature(value: str, signature: str) -> bool:
    return hmac.compare_digest(sign(value), signature or "")


def hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return sign("ip:" + ip)[:24]


def numeric_code(digits: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(digits))


# ---------------------------------------------------------------------------
# Rate limiting (sliding window, in-process).
# For multi-instance deployments swap this for a Redis-backed limiter.
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit; return True if allowed, False if over the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()

# name -> (limit, window seconds)
LIMITS = {
    "signup": (5, 3600),
    "login": (10, 900),
    "login_email": (8, 900),
    "password_reset": (5, 3600),
    "verify_email_resend": (5, 3600),
    "otp_send": (5, 3600),
    "otp_send_phone": (3, 900),
    "otp_verify": (10, 900),
    "mfa_verify": (20, 900),
    "mfa_manage": (20, 900),
    "assist": (30, 3600),
    "swipe": (120, 60),
    "message": (30, 60),
    "upload": (20, 600),
    "report": (20, 3600),
    "typing": (60, 60),
    "phone_share": (10, 3600),
    "generic_write": (120, 60),
}


def allow(name: str, *parts: str) -> bool:
    limit, window = LIMITS[name]
    return rate_limiter.hit(name + ":" + ":".join(p or "-" for p in parts), limit, window)
