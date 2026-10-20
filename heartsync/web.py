"""HTTP plumbing shared by routes: errors, validation, auth guards, CSRF."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from . import db as dbm
from .config import get_settings

T = TypeVar("T", bound=BaseModel)


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields or {}


def bad_request(message: str, fields: dict | None = None, code: str = "invalid_request") -> APIError:
    return APIError(400, code, message, fields)


def not_found(message: str = "Not found.") -> APIError:
    # Used for both "missing" and "not yours" to avoid leaking existence (IDOR).
    return APIError(404, "not_found", message)


def forbidden(message: str = "You don't have permission to do that.", code: str = "forbidden") -> APIError:
    return APIError(403, code, message)


def too_many(message: str = "Too many requests. Please wait a moment and try again.") -> APIError:
    return APIError(429, "rate_limited", message)


def ok(data: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def error_response(err: APIError) -> JSONResponse:
    body = {"ok": False, "error": {"code": err.code, "message": err.message}}
    if err.fields:
        body["error"]["fields"] = err.fields
    return JSONResponse(body, status_code=err.status)


async def parse(request: Request, model: Type[T]) -> T:
    try:
        raw = await request.body()
        if len(raw) > 64 * 1024:
            raise bad_request("Request body is too large.")
        payload = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise bad_request("Malformed JSON body.")
    if not isinstance(payload, dict):
        raise bad_request("Expected a JSON object.")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        fields: dict[str, str] = {}
        for e in exc.errors():
            key = ".".join(str(p) for p in e["loc"]) or "_"
            msg = e["msg"]
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            fields.setdefault(key, msg)
        raise bad_request("Please check the highlighted fields.", fields, code="validation_error")


def get_db(request: Request) -> sqlite3.Connection:
    conn = getattr(request.state, "db", None)
    if conn is None:
        conn = dbm.connect()
        request.state.db = conn
    return conn


def client_ip(request: Request) -> str:
    # Trust X-Forwarded-For only when explicitly configured behind a proxy.
    import os

    if os.environ.get("TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------
@dataclass
class CurrentUser:
    id: str
    email: str
    role: str
    status: str
    email_verified: bool
    phone_verified: bool
    onboarded: bool
    display_name: str | None
    session_id: str
    csrf_token: str
    date_of_birth: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_staff(self) -> bool:
        return self.role in ("admin", "moderator")

    @property
    def can_date(self) -> bool:
        s = get_settings()
        return (
            self.status == "active"
            and self.email_verified
            and (self.phone_verified or not s.require_phone_verification)
            and self.onboarded
        )

    def next_step(self) -> str | None:
        """Where an incomplete account should go next."""
        s = get_settings()
        if self.status == "suspended":
            return "/account/suspended"
        if self.status == "deactivated":
            return "/settings/account"
        if not self.email_verified:
            return "/verify-email"
        if s.require_phone_verification and not self.phone_verified:
            return "/verify-phone"
        if not self.onboarded:
            return "/onboarding"
        return None


def current_user(request: Request) -> CurrentUser | None:
    return getattr(request.state, "user", None)


def require_user(request: Request, allow_suspended: bool = False) -> CurrentUser:
    user = current_user(request)
    if user is None:
        raise APIError(401, "unauthenticated", "Please sign in.")
    if user.status == "suspended" and not allow_suspended:
        raise forbidden("Your account is suspended.", code="suspended")
    return user


def require_dating(request: Request) -> CurrentUser:
    """Guard for every dating feature: verified, active, onboarded adult."""
    user = require_user(request)
    if not user.can_date:
        raise APIError(
            403, "verification_required",
            "Finish verifying your account and profile to use dating features.",
            {"next": user.next_step() or ""},
        )
    return user


def require_staff(request: Request) -> CurrentUser:
    user = current_user(request)
    if user is None:
        raise APIError(401, "unauthenticated", "Please sign in.")
    if not user.is_staff or user.status != "active":
        raise forbidden()
    return user


def require_admin(request: Request) -> CurrentUser:
    user = require_staff(request)
    if not user.is_admin:
        raise forbidden()
    return user


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def safe_next(url: str | None, default: str = "/discover") -> str:
    """Only allow local redirects (prevents open redirects)."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default
