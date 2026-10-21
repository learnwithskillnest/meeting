"""JSON API. Every handler validates input server-side and enforces auth."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import Literal

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .. import catalog
from .. import db as dbm
from .. import security
from ..config import get_settings
from ..providers import payments, storage
from ..realtime import hub
from ..services import (accounts, admin, assist, billing, chat, discovery, matching, mfa, phone, profiles,
                        safety)
from ..services.notifications import get_prefs, set_prefs, unread_count
from ..web import (APIError, bad_request, client_ip, error_response, get_db, not_found, ok, parse,
                   require_admin, require_dating, require_staff, require_user, too_many)

log = logging.getLogger("heartsync.api")
routes: list[Route] = []


def api(path: str, methods: list[str]):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(request: Request):
            try:
                return await fn(request)
            except APIError as err:
                return error_response(err)
        routes.append(Route(path, wrapper, methods=methods))
        return wrapper
    return deco


async def read_upload(request: Request, field: str = "file") -> bytes:
    s = get_settings()
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > s.max_upload_bytes + 64 * 1024:
        raise APIError(413, "too_large", f"Files must be smaller than {s.max_upload_bytes // (1024 * 1024)} MB.")
    try:
        form = await request.form(max_files=1, max_fields=5)
    except Exception:
        raise bad_request("Upload a file using multipart/form-data.")
    upload = form.get(field)
    if upload is None or isinstance(upload, str):
        raise bad_request("No file was uploaded.", {field: "Required."})
    data = await upload.read(s.max_upload_bytes + 1)
    await upload.close()
    if len(data) > s.max_upload_bytes:
        raise APIError(413, "too_large", f"Files must be smaller than {s.max_upload_bytes // (1024 * 1024)} MB.")
    return data


def _set_session_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie("hs_session", token, max_age=s.session_days * 86400, httponly=True, secure=s.cookie_secure,
                    samesite="lax", path="/")


# ===========================================================================
# Auth & account
# ===========================================================================
class SignupIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    date_of_birth: str = Field(max_length=10)
    accept_terms: bool
    confirm_adult: bool


@api("/api/auth/signup", ["POST"])
async def signup(request: Request):
    ip = client_ip(request)
    if not security.allow("signup", ip):
        raise too_many("Too many sign-ups from this network. Please try later.")
    body = await parse(request, SignupIn)
    if not (body.accept_terms and body.confirm_adult):
        raise bad_request("Please confirm you're 18+ and accept the Terms and Privacy Policy.",
                          {"accept_terms": "Required."})
    conn = get_db(request)
    uid = accounts.register(conn, body.email, body.password, body.date_of_birth, security.hash_ip(ip))
    token, _ = accounts.create_session(conn, uid, request.headers.get("user-agent"), security.hash_ip(ip))
    resp = ok({"next": "/verify-email"}, status=201)
    _set_session_cookie(resp, token)
    return resp


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


@api("/api/auth/login", ["POST"])
async def login(request: Request):
    body = await parse(request, LoginIn)
    ip = client_ip(request)
    if not security.allow("login", ip) or not security.allow("login_email", accounts.normalize_email(body.email)):
        raise too_many("Too many sign-in attempts. Please wait 15 minutes or reset your password.")
    conn = get_db(request)
    uid = accounts.authenticate(conn, body.email, body.password)
    if not uid:
        raise APIError(401, "invalid_credentials", "Email or password is incorrect.")
    if mfa.is_enabled(conn, uid):
        # Password is right but no session yet: the second factor comes first.
        challenge = mfa.start_challenge(conn, uid)
        resp = ok({"mfa_required": True})
        s = get_settings()
        resp.set_cookie(MFA_COOKIE, challenge, max_age=mfa.CHALLENGE_MINUTES * 60, httponly=True,
                        secure=s.cookie_secure, samesite="strict", path="/api/auth/mfa")
        return resp
    return _finish_login(request, conn, uid, ip)


MFA_COOKIE = "hs_mfa"


def _finish_login(request: Request, conn, uid: str, ip: str) -> Response:
    token, _ = accounts.create_session(conn, uid, request.headers.get("user-agent"), security.hash_ip(ip))
    user = accounts.load_session(conn, token)
    resp = ok({"next": (user.next_step() if user else None) or "/discover"})
    _set_session_cookie(resp, token)
    return resp


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)


class MfaPasswordIn(BaseModel):
    password: str = Field(max_length=200)


class MfaDisableIn(BaseModel):
    password: str = Field(max_length=200)
    code: str = Field(min_length=1, max_length=20)


@api("/api/auth/mfa/verify", ["POST"])
async def mfa_verify(request: Request):
    body = await parse(request, MfaCodeIn)
    ip = client_ip(request)
    if not security.allow("mfa_verify", ip):
        raise too_many("Too many attempts. Please wait a few minutes.")
    conn = get_db(request)
    uid, _method = mfa.complete_challenge(conn, request.cookies.get(MFA_COOKIE), body.code)
    resp = _finish_login(request, conn, uid, ip)
    resp.delete_cookie(MFA_COOKIE, path="/api/auth/mfa")
    return resp


@api("/api/auth/mfa", ["GET"])
async def mfa_status(request: Request):
    user = require_user(request)
    return ok(mfa.status(get_db(request), user.id))


@api("/api/auth/mfa/setup", ["POST"])
async def mfa_setup(request: Request):
    user = require_user(request)
    body = await parse(request, MfaPasswordIn)
    if not security.allow("mfa_manage", user.id):
        raise too_many()
    return ok(mfa.begin_setup(get_db(request), user, body.password))


@api("/api/auth/mfa/enable", ["POST"])
async def mfa_enable(request: Request):
    user = require_user(request)
    body = await parse(request, MfaCodeIn)
    if not security.allow("mfa_manage", user.id):
        raise too_many()
    conn = get_db(request)
    codes = mfa.confirm_setup(conn, user, body.code)
    # Turning on 2FA signs out every other device.
    accounts.revoke_other_sessions(conn, user.id, user.session_id)
    return ok({"recovery_codes": codes})


@api("/api/auth/mfa/disable", ["POST"])
async def mfa_disable(request: Request):
    user = require_user(request)
    body = await parse(request, MfaDisableIn)
    if not security.allow("mfa_manage", user.id):
        raise too_many()
    mfa.disable(get_db(request), user, body.password, body.code)
    return ok()


@api("/api/auth/mfa/recovery-codes", ["POST"])
async def mfa_recovery(request: Request):
    user = require_user(request)
    body = await parse(request, MfaPasswordIn)
    if not security.allow("mfa_manage", user.id):
        raise too_many()
    return ok({"recovery_codes": mfa.regenerate_recovery_codes(get_db(request), user, body.password)})


@api("/api/auth/logout", ["POST"])
async def logout(request: Request):
    user = require_user(request, allow_suspended=True)
    accounts.revoke_session(get_db(request), user.session_id)
    resp = ok()
    resp.delete_cookie("hs_session", path="/")
    return resp


@api("/api/auth/logout-others", ["POST"])
async def logout_others(request: Request):
    user = require_user(request, allow_suspended=True)
    n = accounts.revoke_other_sessions(get_db(request), user.id, user.session_id)
    return ok({"revoked": n})


@api("/api/auth/sessions", ["GET"])
async def sessions(request: Request):
    user = require_user(request, allow_suspended=True)
    items = accounts.list_sessions(get_db(request), user.id)
    for it in items:
        it["current"] = it["id"] == user.session_id
    return ok(items)


@api("/api/auth/sessions/{sid}", ["DELETE"])
async def revoke_one(request: Request):
    user = require_user(request, allow_suspended=True)
    conn = get_db(request)
    sid = request.path_params["sid"]
    if not dbm.one(conn, "SELECT 1 FROM sessions WHERE id=? AND user_id=?", (sid, user.id)):
        raise not_found()
    accounts.revoke_session(conn, sid)
    return ok()


@api("/api/auth/verify-email/resend", ["POST"])
async def resend_verification(request: Request):
    user = require_user(request)
    if user.email_verified:
        return ok({"already_verified": True})
    if not security.allow("verify_email_resend", user.id):
        raise too_many()
    conn = get_db(request)
    with dbm.tx(conn):
        accounts.send_verification_email(conn, user.id, user.email)
    return ok()


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


@api("/api/auth/password/forgot", ["POST"])
async def forgot(request: Request):
    body = await parse(request, EmailIn)
    ip = client_ip(request)
    if not security.allow("password_reset", ip):
        raise too_many()
    if security.allow("password_reset", "email", accounts.normalize_email(body.email)):
        accounts.request_password_reset(get_db(request), body.email)
    # Same response whether or not the account exists.
    return ok({"message": "If an account exists for that email, we've sent a reset link."})


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


@api("/api/auth/password/reset", ["POST"])
async def reset(request: Request):
    body = await parse(request, ResetIn)
    if not security.allow("password_reset", client_ip(request)):
        raise too_many()
    accounts.reset_password(get_db(request), body.token, body.password)
    return ok({"next": "/login?reset=1"})


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


@api("/api/auth/password/change", ["POST"])
async def change_password(request: Request):
    user = require_user(request)
    body = await parse(request, ChangePasswordIn)
    accounts.change_password(get_db(request), user, body.current_password, body.new_password)
    return ok()


class PhoneIn(BaseModel):
    phone: str = Field(max_length=25)


@api("/api/phone/send-otp", ["POST"])
async def phone_send(request: Request):
    user = require_user(request)
    if not user.email_verified:
        raise APIError(403, "verification_required", "Verify your email first.", {"next": "/verify-email"})
    body = await parse(request, PhoneIn)
    data = accounts.send_phone_otp(get_db(request), user, body.phone, client_ip(request))
    data["dev_mode"] = get_settings().sms_provider == "console"
    return ok(data)


class OtpIn(BaseModel):
    code: str = Field(max_length=10)


@api("/api/phone/verify", ["POST"])
async def phone_verify(request: Request):
    user = require_user(request)
    body = await parse(request, OtpIn)
    accounts.verify_phone_otp(get_db(request), user, body.code, client_ip(request))
    return ok({"next": "/onboarding" if not user.onboarded else "/settings/account"})


@api("/api/me", ["GET"])
async def me(request: Request):
    user = require_user(request, allow_suspended=True)
    conn = get_db(request)
    return ok({
        "id": user.id, "email": user.email, "role": user.role, "status": user.status,
        "email_verified": user.email_verified, "phone_verified": user.phone_verified, "onboarded": user.onboarded,
        "display_name": user.display_name, "next": user.next_step(), "can_date": user.can_date,
        "entitlements": billing.entitlements(conn, user.id),
    })


@api("/api/counts", ["GET"])
async def counts(request: Request):
    user = require_user(request)
    conn = get_db(request)
    return ok({"notifications": unread_count(conn, user.id), "messages": chat.total_unread(conn, user.id) if user.can_date else 0})


@api("/api/account/deactivate", ["POST"])
async def deactivate(request: Request):
    user = require_user(request)
    conn = get_db(request)
    accounts.deactivate(conn, user)
    accounts.revoke_other_sessions(conn, user.id, None)
    resp = ok({"next": "/"})
    resp.delete_cookie("hs_session", path="/")
    return resp


class DeleteIn(BaseModel):
    password: str = Field(max_length=200)
    confirm: Literal["DELETE"]


@api("/api/account/delete", ["POST"])
async def delete_account(request: Request):
    user = require_user(request, allow_suspended=True)
    body = await parse(request, DeleteIn)
    accounts.delete_account(get_db(request), user, body.password)
    resp = ok({"next": "/?deleted=1"})
    resp.delete_cookie("hs_session", path="/")
    return resp


@api("/api/account/export", ["GET"])
async def export_data(request: Request):
    """Data portability: everything we hold about the requesting member."""
    user = require_user(request, allow_suspended=True)
    conn = get_db(request)
    q = lambda sql, *p: [dict(r) for r in dbm.all_(conn, sql, p)]  # noqa: E731
    data = {
        "account": q("SELECT id, email, date_of_birth, role, status, created_at, email_verified_at FROM users WHERE id=?", user.id),
        "contact": q("SELECT phone_e164, phone_verified_at FROM user_contacts WHERE user_id=?", user.id),
        "profile": profiles.own_profile(conn, user.id),
        "preferences": discovery.get_preferences(conn, user.id),
        "swipes": q("SELECT to_user, kind, created_at FROM swipes WHERE from_user=?", user.id),
        "matches": q("SELECT id, user_a, user_b, created_at, unmatched_at FROM matches WHERE user_a=? OR user_b=?", user.id, user.id),
        "messages_sent": q("SELECT conversation_id, kind, body, created_at FROM messages WHERE sender_id=?", user.id),
        "blocks": q("SELECT blocked_id, created_at FROM blocks WHERE blocker_id=?", user.id),
        "reports_made": q("SELECT reported_user_id, category, details, status, created_at FROM reports WHERE reporter_id=?", user.id),
        "subscriptions": q("SELECT plan, status, current_period_end, created_at FROM subscriptions WHERE user_id=?", user.id),
        "payments": billing.history(conn, user.id),
        "phone_shares": q("SELECT recipient_id, created_at, revoked_at FROM phone_shares WHERE owner_id=?", user.id),
    }
    data["profile"]["photos"] = [{"id": p["id"]} for p in data["profile"].get("photos", [])]
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="heartsync-data.json"'})


# ===========================================================================
# Profile
# ===========================================================================
def _require_profile_editor(request: Request):
    user = require_user(request)
    if not user.email_verified:
        raise APIError(403, "verification_required", "Verify your email first.", {"next": "/verify-email"})
    if get_settings().require_phone_verification and not user.phone_verified:
        raise APIError(403, "verification_required", "Verify your phone number first.", {"next": "/verify-phone"})
    return user


@api("/api/profile", ["GET"])
async def get_profile(request: Request):
    user = require_user(request)
    conn = get_db(request)
    data = profiles.own_profile(conn, user.id)
    data["photo_verification"] = profiles.photo_verification_status(conn, user.id)
    return ok(data)


@api("/api/profile", ["PUT"])
async def put_profile(request: Request):
    user = _require_profile_editor(request)
    body = await parse(request, profiles.ProfileIn)
    return ok(profiles.save_profile(get_db(request), user, body))


@api("/api/profile/complete", ["POST"])
async def complete_profile(request: Request):
    user = _require_profile_editor(request)
    body = await parse(request, profiles.ProfileIn)
    return ok(profiles.save_profile(get_db(request), user, body, finish_onboarding=True))


@api("/api/profile/photos", ["POST"])
async def upload_photo(request: Request):
    user = _require_profile_editor(request)
    if not security.allow("upload", user.id):
        raise too_many()
    data = await read_upload(request)
    return ok(profiles.add_photo(get_db(request), user, data), status=201)


@api("/api/profile/photos/{pid}", ["DELETE"])
async def delete_photo(request: Request):
    user = _require_profile_editor(request)
    profiles.delete_photo(get_db(request), user, request.path_params["pid"])
    return ok()


class OrderIn(BaseModel):
    ids: list[str] = Field(max_length=20)


@api("/api/profile/photos/order", ["POST"])
async def order_photos(request: Request):
    user = _require_profile_editor(request)
    body = await parse(request, OrderIn)
    profiles.reorder_photos(get_db(request), user, body.ids)
    return ok()


class VisibilityIn(BaseModel):
    visible: bool | None = None
    show_online: bool | None = None
    show_last_seen: bool | None = None


@api("/api/profile/visibility", ["POST"])
async def visibility(request: Request):
    user = require_user(request)
    body = await parse(request, VisibilityIn)
    profiles.set_visibility(get_db(request), user, body.visible, body.show_online, body.show_last_seen)
    return ok()


@api("/api/profile/verification", ["POST"])
async def photo_verification(request: Request):
    user = require_dating(request)
    if not security.allow("upload", user.id):
        raise too_many()
    data = await read_upload(request)
    profiles.request_photo_verification(get_db(request), user, data)
    return ok({"status": "pending"}, status=201)


class AssistIn(BaseModel):
    kind: Literal["tips", "bio"]
    tone: Literal["warm", "playful", "direct"] = "warm"


@api("/api/profile/assist", ["POST"])
async def profile_assist(request: Request):
    """Writing suggestions for the member's own profile. Never saved automatically."""
    user = require_user(request)
    body = await parse(request, AssistIn)
    if not security.allow("assist", user.id):
        raise too_many("You've asked for a lot of suggestions. Try again in a little while.")
    conn = get_db(request)
    if not profiles.get_raw(conn, user.id):
        raise bad_request("Fill in the basics first, then ask for suggestions.", code="profile_missing")
    if body.kind == "tips":
        return ok({"tips": assist.tips(conn, user), "label": "Suggestions to strengthen your profile"})
    return ok(assist.bio_draft(conn, user, body.tone))


@api("/api/profiles/{uid}", ["GET"])
async def view_profile(request: Request):
    user = require_dating(request)
    conn = get_db(request)
    target = request.path_params["uid"]
    prof = profiles.public_profile(conn, user.id, target)
    prof["saved"] = bool(dbm.one(conn, "SELECT 1 FROM saved_profiles WHERE user_id=? AND saved_user_id=?", (user.id, target)))
    swipe = dbm.one(conn, "SELECT kind FROM swipes WHERE from_user=? AND to_user=?", (user.id, target))
    prof["my_swipe"] = swipe["kind"] if swipe else None
    return ok(prof)


@api("/api/catalog", ["GET"])
async def get_catalog(request: Request):
    conn = get_db(request)
    interests = [dict(r) for r in dbm.all_(conn, "SELECT slug, label, category FROM interests ORDER BY category, label")]
    return ok({
        "genders": catalog.GENDERS, "relationship_goals": catalog.RELATIONSHIP_GOALS,
        "select_fields": catalog.SELECT_FIELDS, "multi_fields": catalog.MULTI_FIELDS, "languages": catalog.LANGUAGES,
        "cities": catalog.cities(), "regions": catalog.regions(), "interests": interests,
        "visibility_fields": catalog.VISIBILITY_FIELDS, "report_categories": safety.REPORT_CATEGORIES,
    })


# ===========================================================================
# Discovery & matching
# ===========================================================================
@api("/api/discover", ["POST"])
async def discover(request: Request):
    user = require_dating(request)
    f = await parse(request, discovery.Filters)
    return ok(discovery.search(get_db(request), user, f))


class PrefsIn(BaseModel):
    age_min: int = Field(ge=18, le=100)
    age_max: int = Field(ge=18, le=100)
    max_distance_km: int | None = None
    nearby_enabled: bool = False


@api("/api/preferences", ["GET", "PUT"])
async def preferences(request: Request):
    user = require_dating(request) if request.method == "PUT" else require_user(request)
    conn = get_db(request)
    if request.method == "PUT":
        body = await parse(request, PrefsIn)
        discovery.set_preferences(conn, user, body.age_min, body.age_max, body.max_distance_km, body.nearby_enabled)
    return ok(discovery.get_preferences(conn, user.id))


class PresetIn(BaseModel):
    name: str = Field(max_length=40)
    filters: discovery.Filters


@api("/api/presets", ["GET", "POST"])
async def presets(request: Request):
    user = require_dating(request)
    conn = get_db(request)
    if request.method == "POST":
        body = await parse(request, PresetIn)
        return ok(discovery.save_preset(conn, user, body.name, body.filters), status=201)
    return ok(discovery.list_presets(conn, user.id))


@api("/api/presets/{pid}", ["DELETE"])
async def delete_preset(request: Request):
    user = require_dating(request)
    discovery.delete_preset(get_db(request), user.id, request.path_params["pid"])
    return ok()


class SwipeIn(BaseModel):
    target_id: str = Field(max_length=64)
    kind: Literal["like", "superlike", "pass"]


@api("/api/swipes", ["POST"])
async def swipe(request: Request):
    user = require_dating(request)
    body = await parse(request, SwipeIn)
    return ok(matching.swipe(get_db(request), user, body.target_id, body.kind))


@api("/api/swipes/undo", ["POST"])
async def undo(request: Request):
    user = require_dating(request)
    return ok(matching.undo(get_db(request), user))


@api("/api/usage", ["GET"])
async def usage(request: Request):
    user = require_dating(request)
    return ok(matching.usage(get_db(request), user.id))


@api("/api/likes/incoming", ["GET"])
async def incoming(request: Request):
    user = require_dating(request)
    return ok(matching.incoming_likes(get_db(request), user))


@api("/api/matches", ["GET"])
async def list_matches(request: Request):
    user = require_dating(request)
    return ok(matching.matches(get_db(request), user))


@api("/api/matches/{mid}", ["DELETE"])
async def unmatch(request: Request):
    user = require_dating(request)
    matching.unmatch(get_db(request), user, request.path_params["mid"])
    return ok()


@api("/api/boost", ["POST"])
async def boost(request: Request):
    user = require_dating(request)
    return ok(matching.boost(get_db(request), user))


@api("/api/saved", ["GET"])
async def saved(request: Request):
    user = require_dating(request)
    return ok(matching.saved_profiles(get_db(request), user))


@api("/api/saved/{uid}", ["POST", "DELETE"])
async def save_toggle(request: Request):
    user = require_dating(request)
    matching.save_profile(get_db(request), user, request.path_params["uid"], request.method == "POST")
    return ok()


# ===========================================================================
# Chat
# ===========================================================================
@api("/api/conversations", ["GET"])
async def conversations(request: Request):
    user = require_dating(request)
    q = request.query_params.get("q")
    archived = request.query_params.get("archived") == "1"
    return ok(chat.list_conversations(get_db(request), user, q, archived))


def _int_param(request: Request, name: str) -> int | None:
    v = request.query_params.get(name)
    if v is None or v == "":
        return None
    if not v.isdigit():
        raise bad_request(f"Invalid {name}.")
    return int(v)


@api("/api/conversations/{cid}/messages", ["GET", "POST"])
async def messages(request: Request):
    user = require_dating(request)
    conn = get_db(request)
    cid = request.path_params["cid"]
    if request.method == "GET":
        return ok(chat.get_messages(conn, user, cid, _int_param(request, "before"), _int_param(request, "after")))

    class MessageIn(BaseModel):
        body: str = Field(max_length=10000)
        reply_to_id: int | None = None
        client_id: str | None = Field(default=None, max_length=64)

    body = await parse(request, MessageIn)
    return ok(chat.send_message(conn, user, cid, body.body, body.reply_to_id, body.client_id), status=201)


@api("/api/conversations/{cid}/images", ["POST"])
async def send_image(request: Request):
    user = require_dating(request)
    conn = get_db(request)
    cid = request.path_params["cid"]
    chat._conversation_for(conn, user, cid)  # authorize before reading the body
    data = await read_upload(request)
    return ok(chat.send_image(conn, user, cid, data), status=201)


class EditIn(BaseModel):
    body: str = Field(max_length=10000)


@api("/api/messages/{mid:int}", ["PATCH", "DELETE"])
async def message_item(request: Request):
    user = require_dating(request)
    conn = get_db(request)
    mid = request.path_params["mid"]
    if request.method == "DELETE":
        chat.delete_message(conn, user, mid)
        return ok()
    body = await parse(request, EditIn)
    return ok(chat.edit_message(conn, user, mid, body.body))


class ReactIn(BaseModel):
    emoji: str | None = Field(default=None, max_length=8)


@api("/api/messages/{mid:int}/reactions", ["POST"])
async def react(request: Request):
    user = require_dating(request)
    body = await parse(request, ReactIn)
    return ok(chat.react(get_db(request), user, request.path_params["mid"], body.emoji))


class ReadIn(BaseModel):
    up_to_id: int = Field(ge=0)


@api("/api/conversations/{cid}/read", ["POST"])
async def read(request: Request):
    user = require_dating(request)
    body = await parse(request, ReadIn)
    chat.mark_read(get_db(request), user, request.path_params["cid"], body.up_to_id)
    return ok()


@api("/api/conversations/{cid}/typing", ["POST"])
async def typing(request: Request):
    user = require_dating(request)
    chat.typing(get_db(request), user, request.path_params["cid"])
    return ok()


class FlagsIn(BaseModel):
    muted: bool | None = None
    archived: bool | None = None


@api("/api/conversations/{cid}/flags", ["POST"])
async def flags(request: Request):
    user = require_dating(request)
    body = await parse(request, FlagsIn)
    chat.set_flags(get_db(request), user, request.path_params["cid"], body.muted, body.archived)
    return ok()


@api("/api/conversations/{cid}/icebreakers", ["GET"])
async def icebreakers(request: Request):
    user = require_dating(request)
    return ok({"label": "Suggested openers (we never send anything for you)",
               "suggestions": chat.icebreakers(get_db(request), user, request.path_params["cid"])})


async def stream(request: Request):
    """Server-Sent Events stream for realtime chat, typing, notifications."""
    user = getattr(request.state, "user", None)
    if user is None or not user.can_date:
        return JSONResponse({"ok": False, "error": {"code": "unauthenticated", "message": "Please sign in."}},
                            status_code=401)
    session_id = user.session_id
    q = hub.connect(user.id)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            yield f"event: ready\ndata: {json.dumps({'user_id': user.id})}\n\n"
            ticks = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, payload = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {event}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    ticks += 1
                    if ticks % 4 == 0:  # re-check the session roughly every minute
                        conn = dbm.connect()
                        try:
                            row = dbm.one(conn, "SELECT s.revoked_at, u.status FROM sessions s JOIN users u ON u.id=s.user_id"
                                                " WHERE s.id=?", (session_id,))
                        finally:
                            conn.close()
                        if not row or row["revoked_at"] or row["status"] != "active":
                            yield "event: logout\ndata: {}\n\n"
                            break
        finally:
            hub.disconnect(user.id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


routes.append(Route("/api/stream", stream, methods=["GET"]))


# ===========================================================================
# Phone sharing
# ===========================================================================
class ShareIn(BaseModel):
    confirm: bool = False


@api("/api/matches/{mid}/phone", ["GET"])
async def phone_status(request: Request):
    user = require_dating(request)
    return ok(phone.status(get_db(request), user, request.path_params["mid"]))


@api("/api/matches/{mid}/phone/share", ["POST"])
async def phone_share(request: Request):
    user = require_dating(request)
    body = await parse(request, ShareIn)
    return ok(phone.share(get_db(request), user, request.path_params["mid"], body.confirm))


@api("/api/matches/{mid}/phone/revoke", ["POST"])
async def phone_revoke(request: Request):
    user = require_dating(request)
    phone.revoke(get_db(request), user, request.path_params["mid"])
    return ok()


@api("/api/matches/{mid}/phone/reveal", ["GET"])
async def phone_reveal(request: Request):
    user = require_dating(request)
    resp = ok(phone.reveal(get_db(request), user, request.path_params["mid"]))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ===========================================================================
# Safety
# ===========================================================================
class BlockIn(BaseModel):
    user_id: str = Field(max_length=64)


@api("/api/blocks", ["GET", "POST"])
async def blocks(request: Request):
    user = require_user(request)
    conn = get_db(request)
    if request.method == "POST":
        body = await parse(request, BlockIn)
        safety.block(conn, user, body.user_id)
        return ok(status=201)
    return ok(safety.blocked_list(conn, user))


@api("/api/blocks/{uid}", ["DELETE"])
async def unblock(request: Request):
    user = require_user(request)
    safety.unblock(get_db(request), user, request.path_params["uid"])
    return ok()


class ReportIn(BaseModel):
    user_id: str = Field(max_length=64)
    category: str = Field(max_length=40)
    details: str | None = Field(default=None, max_length=2000)
    message_id: int | None = None
    block: bool = True


@api("/api/reports", ["POST"])
async def report(request: Request):
    user = require_user(request)
    body = await parse(request, ReportIn)
    rid = safety.report(get_db(request), user, body.user_id, body.category, body.details, body.message_id, body.block)
    return ok({"id": rid}, status=201)


@api("/api/reports/mine", ["GET"])
async def my_reports(request: Request):
    user = require_user(request)
    return ok(safety.my_reports(get_db(request), user))


class AppealIn(BaseModel):
    message: str = Field(max_length=3000)


@api("/api/appeals", ["POST"])
async def appeal(request: Request):
    user = require_user(request, allow_suspended=True)
    body = await parse(request, AppealIn)
    return ok({"id": safety.submit_appeal(get_db(request), user, body.message)}, status=201)


# ===========================================================================
# Notifications
# ===========================================================================
@api("/api/notifications", ["GET"])
async def notifications_list(request: Request):
    user = require_user(request)
    conn = get_db(request)
    before = _int_param(request, "before") or 2**62
    rows = dbm.all_(conn, "SELECT id, kind, title, body, link, created_at, read_at FROM notifications"
                          " WHERE user_id=? AND id<? ORDER BY id DESC LIMIT 30", (user.id, before))
    return ok({"items": [dict(r) for r in rows], "unread": unread_count(conn, user.id),
               "next_before": rows[-1]["id"] if len(rows) == 30 else None})


class NotifReadIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=100)
    all: bool = False


@api("/api/notifications/read", ["POST"])
async def notifications_read(request: Request):
    user = require_user(request)
    body = await parse(request, NotifReadIn)
    conn = get_db(request)
    if body.all:
        conn.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (dbm.iso(), user.id))
    elif body.ids:
        q = ",".join("?" * len(body.ids))
        conn.execute(f"UPDATE notifications SET read_at=? WHERE user_id=? AND id IN ({q}) AND read_at IS NULL",
                     (dbm.iso(), user.id, *body.ids))
    return ok({"unread": unread_count(conn, user.id)})


class NotifPrefsIn(BaseModel):
    inapp_likes: bool = True
    inapp_messages: bool = True
    email_matches: bool = True
    email_messages: bool = False
    email_marketing: bool = False
    quiet_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@api("/api/notifications/preferences", ["GET", "PUT"])
async def notification_prefs(request: Request):
    user = require_user(request)
    conn = get_db(request)
    if request.method == "PUT":
        body = await parse(request, NotifPrefsIn)
        return ok(set_prefs(conn, user.id, body.model_dump()))
    return ok(get_prefs(conn, user.id))


# ===========================================================================
# Billing
# ===========================================================================
@api("/api/billing", ["GET"])
async def billing_summary(request: Request):
    user = require_user(request)
    conn = get_db(request)
    data = billing.summary(conn, user.id)
    data["plans"] = billing.plans_public()
    data["usage"] = matching.usage(conn, user.id)
    return ok(data)


class CheckoutIn(BaseModel):
    plan: str = Field(max_length=20)


@api("/api/billing/checkout", ["POST"])
async def checkout(request: Request):
    user = require_dating(request)
    body = await parse(request, CheckoutIn)
    return ok({"url": billing.start_checkout(get_db(request), user, body.plan)})


@api("/api/billing/cancel", ["POST"])
async def cancel(request: Request):
    user = require_user(request)
    billing.cancel(get_db(request), user)
    return ok()


@api("/api/billing/resume", ["POST"])
async def resume(request: Request):
    user = require_user(request)
    billing.resume(get_db(request), user)
    return ok()


class MockCompleteIn(BaseModel):
    succeed: bool = True


@api("/api/billing/mock/{cid}/complete", ["POST"])
async def mock_complete(request: Request):
    user = require_dating(request)
    body = await parse(request, MockCompleteIn)
    result = billing.mock_complete(get_db(request), user, request.path_params["cid"], body.succeed)
    return ok({"result": result, "next": "/billing?checkout=" + ("success" if body.succeed else "cancelled")})


@api("/api/webhooks/{provider}", ["POST"])
async def webhook(request: Request):
    """Payment webhooks. CSRF-exempt; authenticated by HMAC signature only."""
    provider = request.path_params["provider"]
    payload = await request.body()
    if len(payload) > 512 * 1024:
        raise APIError(413, "too_large", "Payload too large.")
    try:
        event = payments.parse_webhook(provider, payload, {k.lower(): v for k, v in request.headers.items()})
    except payments.WebhookRejected as exc:
        log.warning("webhook rejected provider=%s reason=%s", provider, exc)
        raise APIError(400, "invalid_signature", "Webhook signature verification failed.")
    except (ValueError, TypeError):
        raise bad_request("Malformed webhook payload.")
    if event is None:
        return ok({"result": "ignored"})
    return ok({"result": billing.process_event(get_db(request), provider, event)})


# ===========================================================================
# Admin
# ===========================================================================
def _page_param(request: Request) -> int:
    return _int_param(request, "page") or 1


@api("/api/admin/metrics", ["GET"])
async def admin_metrics(request: Request):
    require_staff(request)
    conn = get_db(request)
    return ok({"metrics": admin.metrics(conn), "spam": admin.spam_indicators(conn)})


@api("/api/admin/users", ["GET"])
async def admin_users(request: Request):
    require_staff(request)
    qp = request.query_params
    return ok(admin.list_users(get_db(request), qp.get("q"), qp.get("status"), qp.get("flagged") == "1", _page_param(request)))


@api("/api/admin/users/{uid}", ["GET"])
async def admin_user(request: Request):
    actor = require_staff(request)
    conn = get_db(request)
    data = admin.user_detail(conn, request.path_params["uid"])
    from ..services import audit

    audit.log(conn, actor.id, "admin.user_viewed", "user", request.path_params["uid"])
    return ok(data)


class ModIn(BaseModel):
    action: Literal["suspend", "reinstate", "warn", "clear_flag", "set_role", "grant_plan"]
    reason: str | None = Field(default=None, max_length=500)
    role: str | None = None
    plan: str | None = None
    days: int | None = None


@api("/api/admin/users/{uid}/actions", ["POST"])
async def admin_user_action(request: Request):
    actor = require_staff(request)
    body = await parse(request, ModIn)
    conn = get_db(request)
    uid = request.path_params["uid"]
    if body.action == "suspend":
        admin.suspend(conn, actor, uid, body.reason or "")
    elif body.action == "reinstate":
        admin.reinstate(conn, actor, uid, body.reason)
    elif body.action == "warn":
        admin.warn(conn, actor, uid, body.reason or "Guideline reminder")
    elif body.action == "clear_flag":
        admin.clear_flag(conn, actor, uid)
    elif body.action == "set_role":
        admin.set_role(conn, actor, uid, body.role or "")
    elif body.action == "grant_plan":
        admin.grant_plan(conn, actor, uid, body.plan or "", body.days or 0)
    return ok()


@api("/api/admin/reports", ["GET"])
async def admin_reports(request: Request):
    require_staff(request)
    return ok(admin.list_reports(get_db(request), request.query_params.get("status", "active"), _page_param(request)))


@api("/api/admin/reports/{rid}", ["GET"])
async def admin_report(request: Request):
    actor = require_staff(request)
    return ok(admin.report_detail(get_db(request), actor, request.path_params["rid"]))


class ResolveIn(BaseModel):
    action: Literal["dismiss", "warn", "suspend", "reviewing"]
    note: str | None = Field(default=None, max_length=1000)


@api("/api/admin/reports/{rid}/resolve", ["POST"])
async def admin_resolve(request: Request):
    actor = require_staff(request)
    body = await parse(request, ResolveIn)
    admin.resolve_report(get_db(request), actor, request.path_params["rid"], body.action, body.note)
    return ok()


@api("/api/admin/verifications", ["GET"])
async def admin_verifications(request: Request):
    require_staff(request)
    return ok(admin.list_verifications(get_db(request)))


class ReviewIn(BaseModel):
    approve: bool
    notes: str | None = Field(default=None, max_length=500)


@api("/api/admin/verifications/{vid}", ["POST"])
async def admin_review(request: Request):
    actor = require_staff(request)
    body = await parse(request, ReviewIn)
    admin.review_verification(get_db(request), actor, request.path_params["vid"], body.approve, body.notes)
    return ok()


@api("/api/admin/appeals", ["GET"])
async def admin_appeals(request: Request):
    require_staff(request)
    return ok(admin.list_appeals(get_db(request)))


class AppealDecisionIn(BaseModel):
    overturn: bool
    decision: str = Field(max_length=1000)


@api("/api/admin/appeals/{aid}", ["POST"])
async def admin_appeal(request: Request):
    actor = require_staff(request)
    body = await parse(request, AppealDecisionIn)
    admin.decide_appeal(get_db(request), actor, request.path_params["aid"], body.overturn, body.decision)
    return ok()


@api("/api/admin/subscriptions", ["GET"])
async def admin_subs(request: Request):
    require_admin(request)
    return ok(admin.list_subscriptions(get_db(request), _page_param(request)))


@api("/api/admin/audit", ["GET"])
async def admin_audit(request: Request):
    require_admin(request)
    return ok(admin.audit_logs(get_db(request), _page_param(request), request.query_params.get("action")))


class ContentIn(BaseModel):
    title: str = Field(max_length=120)
    body: str = Field(max_length=20000)


@api("/api/admin/content/{slug}", ["GET", "PUT"])
async def admin_content(request: Request):
    actor = require_staff(request)
    conn = get_db(request)
    slug = request.path_params["slug"]
    if request.method == "PUT":
        body = await parse(request, ContentIn)
        admin.save_content(conn, actor, slug, body.title, body.body)
    return ok(admin.get_content(conn, slug))


# ===========================================================================
# Media (signed URLs)
# ===========================================================================
async def media(request: Request):
    key = request.path_params["key"]
    if not storage.check_signature(key, request.query_params.get("e", ""), request.query_params.get("s", "")):
        return Response("Link expired", status_code=403, media_type="text/plain")
    data = storage.read(key)
    if data is None:
        return Response("Not found", status_code=404, media_type="text/plain")
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox"})


routes.append(Route("/media/{key:path}", media, methods=["GET"]))
