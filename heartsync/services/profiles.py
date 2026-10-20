"""Dating profiles: validation, persistence, visibility-aware serialization,
photos and completeness scoring."""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .. import catalog
from .. import db as dbm
from ..config import get_settings
from ..providers import storage
from ..realtime import hub
from ..web import APIError, CurrentUser, bad_request, not_found
from . import audit, relations
from .accounts import age_on


def _clean_text(v: str | None, max_len: int) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split()) if max_len <= 120 else v.strip()
    if len(v) > max_len:
        raise ValueError(f"Keep this under {max_len} characters.")
    return v or None


class ProfileIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    gender: str
    pronouns: str | None = None
    interested_in: list[str] = Field(min_length=1)
    headline: str | None = None
    about: str | None = None
    country: str | None = "IN"
    region: str | None = None
    city: str | None = None
    languages: list[str] = Field(default_factory=list)
    height_cm: int | None = Field(default=None, ge=120, le=230)
    education: str | None = None
    profession: str | None = None
    industry: str | None = None
    work_status: str | None = None
    work_schedule: str | None = None
    religion: str | None = None
    relationship_status: str | None = None
    smoking: str | None = None
    drinking: str | None = None
    diet: str | None = None
    exercise: str | None = None
    pets: str | None = None
    sleep_habits: str | None = None
    travel_interests: list[str] = Field(default_factory=list)
    relationship_goals: list[str] = Field(default_factory=list)
    partner_qualities: list[str] = Field(default_factory=list)
    communication_style: str | None = None
    family_plans: str | None = None
    future_goals: str | None = None
    open_to_relocate: bool | None = None
    interests: list[str] = Field(default_factory=list)
    field_visibility: dict[str, str] = Field(default_factory=dict)

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean_text(v, 40) or ""
        if not v or any(ch in v for ch in "<>@") or any(ch.isdigit() for ch in v[:1]):
            raise ValueError("Use a first name or nickname (letters only, no contact details).")
        return v

    @field_validator("pronouns")
    @classmethod
    def _pronouns(cls, v):
        return _clean_text(v, 30)

    @field_validator("headline")
    @classmethod
    def _headline(cls, v):
        return _clean_text(v, 90)

    @field_validator("about")
    @classmethod
    def _about(cls, v):
        return _clean_text(v, 1200)

    @field_validator("profession")
    @classmethod
    def _profession(cls, v):
        return _clean_text(v, 60)

    @field_validator("future_goals")
    @classmethod
    def _future(cls, v):
        return _clean_text(v, 300)

    @field_validator("gender")
    @classmethod
    def _gender(cls, v):
        if v not in catalog.GENDERS:
            raise ValueError("Choose an option from the list.")
        return v

    @model_validator(mode="after")
    def _enums(self):
        for field, options in catalog.SELECT_FIELDS.items():
            val = getattr(self, field)
            if val is not None and val not in options:
                raise ValueError(f"Invalid value for {field}.")
        for field, options in catalog.MULTI_FIELDS.items():
            vals = getattr(self, field)
            if len(vals) > 12 or any(v not in options for v in vals):
                raise ValueError(f"Invalid values for {field}.")
            setattr(self, field, sorted(set(vals)))
        for k, v in self.field_visibility.items():
            if k not in catalog.VISIBILITY_FIELDS or v not in catalog.VISIBILITY_LEVELS:
                raise ValueError("Invalid visibility setting.")
        if len(self.interests) > 15:
            raise ValueError("Choose up to 15 interests.")
        if self.city and not catalog.city_coords(self.country, self.region, self.city):
            raise ValueError("Choose a city from the list.")
        for text in (self.about, self.headline):
            if text and _looks_like_contact(text):
                raise ValueError("Please don't put phone numbers, emails or social handles in your profile. "
                                 "You can share your number privately with a match.")
        return self


def _looks_like_contact(text: str) -> bool:
    import re

    digits = re.sub(r"\D", "", text)
    return bool(
        re.search(r"(\+?\d[\d\s\-]{8,}\d)", text) or len(digits) >= 10
        or re.search(r"[\w.]+@[\w-]+\.\w+", text)
        or re.search(r"(?i)\b(insta|instagram|snap|snapchat|telegram|whatsapp)\b\s*[:@]?\s*\S+", text)
    )


JSON_FIELDS = ("interested_in", "languages", "travel_interests", "relationship_goals", "partner_qualities")


def get_raw(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return dbm.one(conn, "SELECT * FROM profiles WHERE user_id=?", (user_id,))


def interest_slugs(conn: sqlite3.Connection, user_id: str) -> list[str]:
    return [r[0] for r in dbm.all_(
        conn, "SELECT i.slug FROM profile_interests pi JOIN interests i ON i.id=pi.interest_id WHERE pi.user_id=?"
              " ORDER BY i.label", (user_id,))]


def compute_completeness(row: dict, photo_count: int, interest_count: int) -> int:
    checks = [
        bool(row.get("display_name")), bool(row.get("headline")), len(row.get("about") or "") >= 50,
        photo_count >= 1, photo_count >= 3, interest_count >= 3, bool(row.get("city")),
        bool(json.loads(row.get("languages") or "[]")), bool(row.get("height_cm")), bool(row.get("education")),
        bool(row.get("profession")), bool(json.loads(row.get("relationship_goals") or "[]")),
        bool(row.get("smoking")), bool(row.get("drinking")), bool(row.get("diet")),
        bool(json.loads(row.get("partner_qualities") or "[]")), bool(row.get("communication_style")),
    ]
    return round(100 * sum(checks) / len(checks))


def refresh_completeness(conn: sqlite3.Connection, user_id: str) -> int:
    row = get_raw(conn, user_id)
    if not row:
        return 0
    photos = dbm.scalar(conn, "SELECT COUNT(*) FROM profile_photos WHERE user_id=? AND status='approved'", (user_id,))
    ints = dbm.scalar(conn, "SELECT COUNT(*) FROM profile_interests WHERE user_id=?", (user_id,))
    score = compute_completeness(dict(row), photos, ints)
    conn.execute("UPDATE profiles SET completeness=? WHERE user_id=?", (score, user_id))
    return score


def save_profile(conn: sqlite3.Connection, user: CurrentUser, data: ProfileIn, finish_onboarding: bool = False) -> dict:
    if user.status != "active":
        raise APIError(403, "inactive", "Your account isn't active.")
    s = get_settings()
    coords = catalog.city_coords(data.country, data.region, data.city)
    lat, lng = (round(coords[0], 1), round(coords[1], 1)) if coords else (None, None)
    visibility = dict(catalog.VISIBILITY_FIELDS)
    existing = get_raw(conn, user.id)
    if existing:
        visibility.update(json.loads(existing["field_visibility"] or "{}"))
    visibility.update(data.field_visibility)
    values = data.model_dump(exclude={"interests", "field_visibility"})
    for f in JSON_FIELDS:
        values[f] = json.dumps(values[f])
    values["open_to_relocate"] = None if data.open_to_relocate is None else int(data.open_to_relocate)
    values.update(lat_approx=lat, lng_approx=lng, field_visibility=json.dumps(visibility),
                  updated_at=dbm.iso(), user_id=user.id)
    cols = list(values)
    with dbm.tx(conn):
        if existing:
            sets = ", ".join(f"{c}=:{c}" for c in cols if c != "user_id")
            conn.execute(f"UPDATE profiles SET {sets} WHERE user_id=:user_id", values)
        else:
            values["created_at"] = dbm.iso()
            cols = list(values)
            conn.execute(
                f"INSERT INTO profiles ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", values)
        conn.execute("DELETE FROM profile_interests WHERE user_id=?", (user.id,))
        if data.interests:
            q = ",".join("?" * len(data.interests))
            ids = [r[0] for r in dbm.all_(conn, f"SELECT id FROM interests WHERE slug IN ({q})", tuple(data.interests))]
            conn.executemany("INSERT INTO profile_interests (user_id, interest_id) VALUES (?,?)",
                             [(user.id, i) for i in ids])
        if finish_onboarding:
            photos = dbm.scalar(conn, "SELECT COUNT(*) FROM profile_photos WHERE user_id=?", (user.id,))
            if photos < 1:
                raise bad_request("Add at least one photo to finish your profile.", {"photos": "Add a photo."})
            conn.execute("UPDATE profiles SET onboarded_at=COALESCE(onboarded_at, ?) WHERE user_id=?",
                         (dbm.iso(), user.id))
        refresh_completeness(conn, user.id)
        audit.log(conn, user.id, "profile.updated", "user", user.id)
    _ = s
    return own_profile(conn, user.id)


def set_visibility(conn: sqlite3.Connection, user: CurrentUser, visible: bool | None = None,
                   show_online: bool | None = None, show_last_seen: bool | None = None) -> None:
    if not get_raw(conn, user.id):
        raise bad_request("Create your profile first.")
    with dbm.tx(conn):
        if visible is not None:
            conn.execute("UPDATE profiles SET visibility=? WHERE user_id=?", ("visible" if visible else "hidden", user.id))
        if show_online is not None:
            conn.execute("UPDATE profiles SET show_online_status=? WHERE user_id=?", (int(show_online), user.id))
        if show_last_seen is not None:
            conn.execute("UPDATE profiles SET show_last_seen=? WHERE user_id=?", (int(show_last_seen), user.id))


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def photo_list(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = dbm.all_(conn, "SELECT id, storage_key, position, width, height FROM profile_photos"
                          " WHERE user_id=? AND status='approved' ORDER BY position, created_at", (user_id,))
    return [{"id": r["id"], "url": storage.signed_url(r["storage_key"]), "width": r["width"], "height": r["height"]}
            for r in rows]


def own_profile(conn: sqlite3.Connection, user_id: str) -> dict:
    row = get_raw(conn, user_id)
    contacts = dbm.one(conn, "SELECT phone_e164, phone_verified_at FROM user_contacts WHERE user_id=?", (user_id,))
    u = dbm.one(conn, "SELECT date_of_birth, email, email_verified_at FROM users WHERE id=?", (user_id,))
    out: dict[str, Any] = {"exists": bool(row), "photos": photo_list(conn, user_id),
                           "interests": interest_slugs(conn, user_id),
                           "age": age_on(u["date_of_birth"]) if u else None,
                           "email_verified": bool(u and u["email_verified_at"]),
                           "phone_verified": bool(contacts and contacts["phone_verified_at"]),
                           "phone_masked": None}
    if contacts and contacts["phone_e164"] and contacts["phone_verified_at"]:
        from .accounts import mask_phone

        out["phone_masked"] = mask_phone(contacts["phone_e164"])
    if row:
        d = dict(row)
        for f in JSON_FIELDS:
            d[f] = json.loads(d[f] or "[]")
        d["field_visibility"] = {**catalog.VISIBILITY_FIELDS, **json.loads(d["field_visibility"] or "{}")}
        d.pop("lat_approx", None)
        d.pop("lng_approx", None)
        out.update(d)
    return out


def haversine_km(lat1, lng1, lat2, lng2) -> float | None:
    if None in (lat1, lng1, lat2, lng2):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def distance_label(km: float | None) -> str | None:
    if km is None:
        return None
    if km < 10:
        return "Less than 10 km away"
    if km < 100:
        return f"About {int(round(km, -1))} km away"
    return f"About {int(round(km, -2))} km away"


def last_seen_label(last_active: str | None) -> str | None:
    dt = dbm.parse_iso(last_active)
    if not dt:
        return None
    mins = (dbm.utcnow() - dt).total_seconds() / 60
    if mins < 15:
        return "Active recently"
    if mins < 60 * 24:
        return "Active today"
    if mins < 60 * 24 * 7:
        return "Active this week"
    return None


def public_profile(conn: sqlite3.Connection, viewer_id: str, target_id: str, record_view: bool = True,
                   viewer_is_staff: bool = False) -> dict:
    """Profile as seen by `viewer_id`, with field-level visibility applied.
    Never includes phone, email, exact date of birth or coordinates."""
    if not viewer_is_staff and not relations.can_view_profile(conn, viewer_id, target_id):
        raise not_found("This profile isn't available.")
    row = dbm.one(conn, "SELECT p.*, u.date_of_birth, u.last_active_at, u.created_at AS joined_at,"
                        " c.phone_verified_at FROM profiles p JOIN users u ON u.id=p.user_id"
                        " LEFT JOIN user_contacts c ON c.user_id=u.id WHERE p.user_id=?", (target_id,))
    if not row:
        raise not_found("This profile isn't available.")
    matched = bool(relations.active_match(conn, viewer_id, target_id))
    vis = {**catalog.VISIBILITY_FIELDS, **json.loads(row["field_visibility"] or "{}")}
    allowed_levels = {"everyone", "matches"} if (matched or viewer_id == target_id) else {"everyone"}

    def visible(field: str) -> bool:
        return viewer_id == target_id or viewer_is_staff or vis.get(field, "everyone") in allowed_levels

    viewer_geo = dbm.one(conn, "SELECT lat_approx, lng_approx FROM profiles WHERE user_id=?", (viewer_id,))
    km = haversine_km(viewer_geo["lat_approx"] if viewer_geo else None, viewer_geo["lng_approx"] if viewer_geo else None,
                      row["lat_approx"], row["lng_approx"])
    out: dict[str, Any] = {
        "id": target_id,
        "display_name": row["display_name"],
        "age": age_on(row["date_of_birth"]),
        "gender": row["gender"],
        "pronouns": row["pronouns"],
        "headline": row["headline"],
        "about": row["about"],
        "languages": json.loads(row["languages"] or "[]"),
        "relationship_goals": json.loads(row["relationship_goals"] or "[]"),
        "partner_qualities": json.loads(row["partner_qualities"] or "[]"),
        "travel_interests": json.loads(row["travel_interests"] or "[]"),
        "exercise": row["exercise"], "pets": row["pets"], "communication_style": row["communication_style"],
        "future_goals": row["future_goals"],
        "interests": interest_slugs(conn, target_id),
        "photos": photo_list(conn, target_id),
        "verification": {"phone": bool(row["phone_verified_at"]), "photo": bool(row["photo_verified"])},
        "distance": distance_label(km),
        "matched": matched,
        "completeness": row["completeness"],
    }
    for field in catalog.VISIBILITY_FIELDS:
        out[field] = row[field] if visible(field) else None
    out["region"] = row["region"] if visible("city") else None
    show_activity = bool(row["show_last_seen"]) or viewer_is_staff
    out["online"] = bool(row["show_online_status"]) and matched and hub.is_online(target_id)
    out["last_seen"] = last_seen_label(row["last_active_at"]) if show_activity else None
    if record_view and viewer_id != target_id and not viewer_is_staff:
        conn.execute(
            "INSERT INTO profile_views (viewer_id, viewed_id, seen_at) VALUES (?,?,?)"
            " ON CONFLICT(viewer_id, viewed_id) DO UPDATE SET seen_at=excluded.seen_at",
            (viewer_id, target_id, dbm.iso()),
        )
    return out


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------
def add_photo(conn: sqlite3.Connection, user: CurrentUser, data: bytes) -> dict:
    s = get_settings()
    count = dbm.scalar(conn, "SELECT COUNT(*) FROM profile_photos WHERE user_id=?", (user.id,))
    if count >= s.max_photos:
        raise bad_request(f"You can upload up to {s.max_photos} photos.")
    try:
        stored = storage.save_image(f"photos/{user.id}", data)
    except storage.UploadRejected as exc:
        raise bad_request(str(exc), code="upload_rejected")
    pid = dbm.new_id()
    with dbm.tx(conn):
        pos = (dbm.scalar(conn, "SELECT MAX(position) FROM profile_photos WHERE user_id=?", (user.id,)) or 0) + 1
        conn.execute(
            "INSERT INTO profile_photos (id, user_id, storage_key, position, width, height, created_at) VALUES (?,?,?,?,?,?,?)",
            (pid, user.id, stored.key, pos, stored.width, stored.height, dbm.iso()),
        )
        refresh_completeness(conn, user.id)
    return {"id": pid, "url": storage.signed_url(stored.key)}


def delete_photo(conn: sqlite3.Connection, user: CurrentUser, photo_id: str) -> None:
    row = dbm.one(conn, "SELECT storage_key FROM profile_photos WHERE id=? AND user_id=?", (photo_id, user.id))
    if not row:
        raise not_found("Photo not found.")
    with dbm.tx(conn):
        onboarded = dbm.one(conn, "SELECT onboarded_at FROM profiles WHERE user_id=?", (user.id,))
        count = dbm.scalar(conn, "SELECT COUNT(*) FROM profile_photos WHERE user_id=?", (user.id,))
        if onboarded and onboarded["onboarded_at"] and count <= 1:
            raise bad_request("Your profile needs at least one photo. Add another before removing this one.")
        conn.execute("DELETE FROM profile_photos WHERE id=?", (photo_id,))
        refresh_completeness(conn, user.id)
    storage.delete(row["storage_key"])


def reorder_photos(conn: sqlite3.Connection, user: CurrentUser, ordered_ids: list[str]) -> None:
    owned = {r[0] for r in dbm.all_(conn, "SELECT id FROM profile_photos WHERE user_id=?", (user.id,))}
    if set(ordered_ids) != owned:
        raise bad_request("Photo list doesn't match your photos.")
    with dbm.tx(conn):
        for i, pid in enumerate(ordered_ids):
            conn.execute("UPDATE profile_photos SET position=? WHERE id=? AND user_id=?", (i, pid, user.id))


def request_photo_verification(conn: sqlite3.Connection, user: CurrentUser, data: bytes) -> None:
    if dbm.one(conn, "SELECT 1 FROM photo_verifications WHERE user_id=? AND status='pending'", (user.id,)):
        raise bad_request("You already have a verification request awaiting review.")
    try:
        stored = storage.save_image(f"verifications/{user.id}", data)
    except storage.UploadRejected as exc:
        raise bad_request(str(exc), code="upload_rejected")
    with dbm.tx(conn):
        conn.execute("INSERT INTO photo_verifications (id, user_id, storage_key, created_at) VALUES (?,?,?,?)",
                     (dbm.new_id(), user.id, stored.key, dbm.iso()))
        audit.log(conn, user.id, "verification.photo_requested", "user", user.id)


def photo_verification_status(conn: sqlite3.Connection, user_id: str) -> str | None:
    row = dbm.one(conn, "SELECT status FROM photo_verifications WHERE user_id=? ORDER BY created_at DESC LIMIT 1",
                  (user_id,))
    return row["status"] if row else None
