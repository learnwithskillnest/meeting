"""Discovery, advanced filters and compatibility ranking.

Pipeline:
1. SQL applies eligibility, privacy, blocking, reciprocity and hard filters
   (all with bound parameters and indexed columns), capped at CANDIDATE_CAP.
2. Python computes approximate distance, applies the distance filter, scores
   compatibility with transparent reasons, and sorts.
3. Results are paginated with an opaque cursor bound to the filter set, and
   only the requested page is fully serialized.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .. import catalog
from .. import db as dbm
from ..web import APIError, CurrentUser, bad_request
from . import billing, relations
from .profiles import haversine_km, public_profile

CANDIDATE_CAP = 500
PAGE_SIZE = 20

BASIC_FILTERS = {"age_min", "age_max", "genders", "goals", "languages", "country", "region", "city",
                 "max_distance_km", "sort", "mode", "cursor", "limit", "strict"}


class Filters(BaseModel):
    age_min: int | None = Field(default=None, ge=18, le=100)
    age_max: int | None = Field(default=None, ge=18, le=100)
    genders: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    relationship_status: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    country: str | None = None
    region: str | None = None
    city: str | None = None
    max_distance_km: int | None = Field(default=None, ge=5, le=5000)
    open_to_relocate: bool | None = None
    education: list[str] = Field(default_factory=list)
    profession: str | None = Field(default=None, max_length=60)
    industry: list[str] = Field(default_factory=list)
    work_status: list[str] = Field(default_factory=list)
    height_min: int | None = Field(default=None, ge=120, le=230)
    height_max: int | None = Field(default=None, ge=120, le=230)
    smoking: list[str] = Field(default_factory=list)
    drinking: list[str] = Field(default_factory=list)
    diet: list[str] = Field(default_factory=list)
    exercise: list[str] = Field(default_factory=list)
    pets: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    interests_mode: Literal["any", "all"] = "any"
    religion: list[str] = Field(default_factory=list)
    family_plans: list[str] = Field(default_factory=list)
    communication: list[str] = Field(default_factory=list)
    partner_qualities: list[str] = Field(default_factory=list)
    photo_verified: bool = False
    phone_verified: bool = False
    active_within_days: int | None = Field(default=None, ge=1, le=90)
    joined_within_days: int | None = Field(default=None, ge=1, le=365)
    min_completeness: int | None = Field(default=None, ge=0, le=100)
    common_interests: bool = False
    exclude: dict[str, list[str]] = Field(default_factory=dict)
    strict: bool = True
    sort: Literal["compatibility", "active", "distance", "newest"] = "compatibility"
    mode: Literal["all", "new", "active", "nearby", "daily"] = "all"
    cursor: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=PAGE_SIZE, ge=1, le=50)

    @model_validator(mode="after")
    def _check(self):
        checks = {
            "genders": catalog.GENDERS, "goals": catalog.RELATIONSHIP_GOALS,
            "relationship_status": catalog.RELATIONSHIP_STATUS, "languages": catalog.LANGUAGES,
            "education": catalog.EDUCATION, "industry": catalog.INDUSTRIES, "work_status": catalog.WORK_STATUS,
            "smoking": catalog.SMOKING, "drinking": catalog.DRINKING, "diet": catalog.DIET,
            "exercise": catalog.EXERCISE, "pets": catalog.PETS, "religion": catalog.RELIGION,
            "family_plans": catalog.FAMILY_PLANS, "communication": catalog.COMMUNICATION,
            "partner_qualities": catalog.PARTNER_QUALITIES,
        }
        for field, options in checks.items():
            if any(v not in options for v in getattr(self, field)):
                raise ValueError(f"Invalid option in {field}.")
        for field, vals in self.exclude.items():
            if field not in EXCLUDABLE or any(v not in EXCLUDABLE[field] for v in vals):
                raise ValueError("Invalid exclusion.")
        if self.age_min and self.age_max and self.age_min > self.age_max:
            raise ValueError("Minimum age must be less than maximum age.")
        if self.height_min and self.height_max and self.height_min > self.height_max:
            raise ValueError("Minimum height must be less than maximum height.")
        if len(self.interests) > 20:
            raise ValueError("Too many interests selected.")
        return self

    def active_filters(self) -> dict:
        defaults = Filters.model_construct()
        out = {}
        for name in Filters.model_fields:
            if name in ("cursor", "limit", "sort", "mode", "strict", "interests_mode"):
                continue
            val = getattr(self, name)
            if val != getattr(defaults, name, None) and val not in (None, [], {}, False):
                out[name] = val
        return out

    def fingerprint(self) -> str:
        data = self.model_dump(exclude={"cursor", "limit"})
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]


EXCLUDABLE = {"smoking": catalog.SMOKING, "drinking": catalog.DRINKING, "diet": catalog.DIET, "pets": catalog.PETS}


def is_advanced(f: Filters) -> bool:
    return any(k not in BASIC_FILTERS for k in f.active_filters())


def _dob_bounds(age_min: int, age_max: int) -> tuple[str, str]:
    today = dbm.utcnow().date()

    def years_ago(n: int) -> date:
        try:
            return today.replace(year=today.year - n)
        except ValueError:  # Feb 29
            return today.replace(year=today.year - n, day=28)

    latest = years_ago(age_min)               # born on/before this date => age >= age_min
    earliest = years_ago(age_max + 1) + timedelta(days=1)  # born after => age <= age_max
    return earliest.isoformat(), latest.isoformat()


def _encode_cursor(offset: int, fp: str) -> str:
    return base64.urlsafe_b64encode(f"{offset}:{fp}".encode()).decode()


def _decode_cursor(cursor: str | None, fp: str) -> int:
    if not cursor:
        return 0
    try:
        offset, cfp = base64.urlsafe_b64decode(cursor.encode()).decode().split(":")
        if cfp != fp:
            raise ValueError
        return max(0, int(offset))
    except Exception:
        raise bad_request("This results page has expired. Please search again.", code="bad_cursor")


def search(conn: sqlite3.Connection, viewer: CurrentUser, f: Filters) -> dict:
    ent = billing.entitlements(conn, viewer.id)
    if is_advanced(f) and not ent.get("advanced_filters"):
        raise APIError(402, "upgrade_required", "Advanced filters are part of HeartSync Plus.",
                       {"feature": "advanced_filters"})
    me = dbm.one(conn, "SELECT p.*, d.age_min AS pref_min, d.age_max AS pref_max, d.max_distance_km AS pref_dist,"
                       " d.nearby_enabled FROM profiles p LEFT JOIN dating_preferences d ON d.user_id=p.user_id"
                       " WHERE p.user_id=?", (viewer.id,))
    if not me:
        raise bad_request("Create your profile first.")

    from .accounts import age_on

    my_age = age_on(viewer.date_of_birth)
    my_interests = set(r[0] for r in dbm.all_(conn, "SELECT interest_id FROM profile_interests WHERE user_id=?", (viewer.id,)))
    params: dict = {"viewer": viewer.id, "my_gender": me["gender"], "my_age": my_age, "now": dbm.iso()}
    where = [
        "u.id != :viewer",
        relations.eligible_sql(),
        relations.not_blocked_sql(),
        # Hidden profiles never appear in discovery.
        "p.visibility = 'visible'",
        # Members under automatic risk review are paused from discovery.
        "u.flagged_at IS NULL",
        # Not already swiped or matched.
        "NOT EXISTS (SELECT 1 FROM swipes s WHERE s.from_user=:viewer AND s.to_user=u.id)",
        "NOT EXISTS (SELECT 1 FROM matches m WHERE ((m.user_a=:viewer AND m.user_b=u.id) OR (m.user_b=:viewer AND m.user_a=u.id)))",
        # Reciprocity: they are interested in my gender and my age fits their range.
        "EXISTS (SELECT 1 FROM json_each(p.interested_in) j WHERE j.value = :my_gender)",
        "(d.user_id IS NULL OR (:my_age BETWEEN d.age_min AND d.age_max))",
    ]

    def in_list(column: str, values: list[str], key: str, nullable_ok: bool = False) -> None:
        if not values:
            return
        names = []
        for i, v in enumerate(values):
            params[f"{key}{i}"] = v
            names.append(f":{key}{i}")
        clause = f"{column} IN ({', '.join(names)})"
        if nullable_ok and not f.strict:
            clause = f"({clause} OR {column} IS NULL)"
        where.append(clause)

    def json_any(column: str, values: list[str], key: str) -> None:
        if not values:
            return
        names = []
        for i, v in enumerate(values):
            params[f"{key}{i}"] = v
            names.append(f":{key}{i}")
        clause = f"EXISTS (SELECT 1 FROM json_each({column}) j WHERE j.value IN ({', '.join(names)}))"
        if not f.strict:
            clause = f"({clause} OR json_array_length({column}) = 0)"
        where.append(clause)

    def vis_ok(field: str) -> str:
        # Only filter on fields the target has made visible to everyone.
        return f"COALESCE(json_extract(p.field_visibility, '$.{field}'), '{catalog.VISIBILITY_FIELDS[field]}') = 'everyone'"

    # Age (defaults to my saved preferences)
    age_min = f.age_min or me["pref_min"] or 18
    age_max = f.age_max or me["pref_max"] or 99
    earliest, latest = _dob_bounds(age_min, age_max)
    params.update(dob_earliest=earliest, dob_latest=latest)
    where.append("u.date_of_birth BETWEEN :dob_earliest AND :dob_latest")

    genders = f.genders or json.loads(me["interested_in"] or "[]")
    in_list("p.gender", genders, "g")
    json_any("p.relationship_goals", f.goals, "goal")
    json_any("p.languages", f.languages, "lang")
    if f.country:
        params["country"] = f.country
        where.append("p.country = :country")
    if f.region:
        params["region"] = f.region
        where.append(f"p.region = :region AND {vis_ok('city')}")
    if f.city:
        params["city"] = f.city
        where.append(f"p.city = :city AND {vis_ok('city')}")
    if f.open_to_relocate is not None:
        params["relocate"] = int(f.open_to_relocate)
        where.append("p.open_to_relocate = :relocate")

    for field, values, key in (
        ("relationship_status", f.relationship_status, "rs"), ("education", f.education, "edu"),
        ("industry", f.industry, "ind"), ("work_status", f.work_status, "ws"), ("smoking", f.smoking, "smk"),
        ("drinking", f.drinking, "drk"), ("diet", f.diet, "diet"),
    ):
        if values:
            in_list(f"p.{field}", values, key, nullable_ok=True)
            where.append(f"(p.{field} IS NULL OR {vis_ok(field)})")
    in_list("p.exercise", f.exercise, "ex", nullable_ok=True)
    in_list("p.pets", f.pets, "pet", nullable_ok=True)
    in_list("p.communication_style", f.communication, "comm", nullable_ok=True)
    json_any("p.partner_qualities", f.partner_qualities, "pq")
    # Sensitive attributes: only match people who chose to show them publicly,
    # and never include unknown values (no inference).
    if f.religion:
        in_list("p.religion", f.religion, "rel")
        where.append(vis_ok("religion"))
    if f.family_plans:
        in_list("p.family_plans", f.family_plans, "fp")
        where.append(vis_ok("family_plans"))
    if f.profession:
        params["profession"] = f"%{f.profession.lower()}%"
        where.append(f"lower(p.profession) LIKE :profession AND {vis_ok('profession')}")
    if f.height_min or f.height_max:
        params["hmin"], params["hmax"] = f.height_min or 0, f.height_max or 999
        clause = f"(p.height_cm BETWEEN :hmin AND :hmax AND {vis_ok('height_cm')})"
        where.append(clause if f.strict else f"({clause} OR p.height_cm IS NULL)")
    for field, values in f.exclude.items():
        for i, v in enumerate(values):
            params[f"x{field}{i}"] = v
            where.append(f"(p.{field} IS NULL OR p.{field} != :x{field}{i})")
    if f.interests:
        names = []
        for i, slug in enumerate(f.interests):
            params[f"int{i}"] = slug
            names.append(f":int{i}")
        sub = (f"SELECT COUNT(DISTINCT i.slug) FROM profile_interests pi JOIN interests i ON i.id=pi.interest_id"
               f" WHERE pi.user_id=u.id AND i.slug IN ({', '.join(names)})")
        where.append(f"({sub}) >= {len(f.interests) if f.interests_mode == 'all' else 1}")
    if f.common_interests:
        where.append("EXISTS (SELECT 1 FROM profile_interests a JOIN profile_interests b ON a.interest_id=b.interest_id"
                     " WHERE a.user_id=u.id AND b.user_id=:viewer)")
    if f.photo_verified:
        where.append("p.photo_verified = 1")
    if f.phone_verified:
        where.append("EXISTS (SELECT 1 FROM user_contacts c2 WHERE c2.user_id=u.id AND c2.phone_verified_at IS NOT NULL)")
    active_days = f.active_within_days or (7 if f.mode == "active" else None)
    if active_days:
        params["active_since"] = dbm.iso_in(days=-active_days)
        where.append("u.last_active_at >= :active_since AND p.show_last_seen = 1")
    joined_days = f.joined_within_days or (14 if f.mode == "new" else None)
    if joined_days:
        params["joined_since"] = dbm.iso_in(days=-joined_days)
        where.append("u.created_at >= :joined_since")
    if f.min_completeness:
        params["min_comp"] = f.min_completeness
        where.append("p.completeness >= :min_comp")

    max_km = f.max_distance_km
    if f.mode == "nearby":
        if not me["nearby_enabled"]:
            raise APIError(400, "nearby_disabled", "Turn on Nearby in your discovery settings to use this.",
                           {"next": "/settings/discovery"})
        max_km = max_km or 50
    max_km = max_km or me["pref_dist"]
    if max_km:
        if me["lat_approx"] is None:
            raise bad_request("Set your city in your profile to filter by distance.", code="location_unavailable")
        deg = max_km / 111.0 + 0.2
        params.update(lat_lo=me["lat_approx"] - deg, lat_hi=me["lat_approx"] + deg,
                      lng_lo=me["lng_approx"] - deg * 1.5, lng_hi=me["lng_approx"] + deg * 1.5)
        where.append("p.lat_approx BETWEEN :lat_lo AND :lat_hi AND p.lng_approx BETWEEN :lng_lo AND :lng_hi")

    sql = (
        "SELECT u.id, u.created_at, u.last_active_at, p.lat_approx, p.lng_approx, p.relationship_goals, p.languages,"
        " p.smoking, p.drinking, p.diet, p.completeness, p.boost_until, p.show_last_seen,"
        " (SELECT seen_at FROM profile_views v WHERE v.viewer_id=:viewer AND v.viewed_id=u.id) AS seen_at"
        " FROM users u JOIN profiles p ON p.user_id=u.id LEFT JOIN dating_preferences d ON d.user_id=u.id"
        f" WHERE {' AND '.join(where)} ORDER BY u.last_active_at DESC LIMIT {CANDIDATE_CAP}"
    )
    rows = dbm.all_(conn, sql, params)
    candidate_ids = [r["id"] for r in rows]
    interests_by_user: dict[str, set[int]] = {}
    if candidate_ids:
        q = ",".join("?" * len(candidate_ids))
        for r in dbm.all_(conn, f"SELECT user_id, interest_id FROM profile_interests WHERE user_id IN ({q})",
                          tuple(candidate_ids)):
            interests_by_user.setdefault(r[0], set()).add(r[1])
    labels = {r[0]: r[1] for r in dbm.all_(conn, "SELECT id, label FROM interests")}
    my_goals = set(json.loads(me["relationship_goals"] or "[]"))
    my_langs = set(json.loads(me["languages"] or "[]"))

    scored = []
    now = dbm.iso()
    for r in rows:
        km = haversine_km(me["lat_approx"], me["lng_approx"], r["lat_approx"], r["lng_approx"])
        if max_km and (km is None or km > max_km):
            continue
        score, reasons = compatibility(
            my_interests, interests_by_user.get(r["id"], set()), labels, my_goals,
            set(json.loads(r["relationship_goals"] or "[]")), my_langs, set(json.loads(r["languages"] or "[]")),
            (me["smoking"], me["drinking"], me["diet"]), (r["smoking"], r["drinking"], r["diet"]), km, r["completeness"],
        )
        if r["seen_at"] and r["seen_at"] > dbm.iso_in(hours=-24):
            score -= 10
        boosted = bool(r["boost_until"] and r["boost_until"] > now)
        rank = score + (15 if boosted else 0)
        scored.append({"id": r["id"], "score": max(0, min(100, score)), "rank": rank, "reasons": reasons, "km": km,
                       "created_at": r["created_at"],
                       "last_active_at": r["last_active_at"] if r["show_last_seen"] else ""})

    if f.sort == "distance" or f.mode == "nearby":
        scored.sort(key=lambda x: (x["km"] is None, x["km"] or 0))
    elif f.sort == "active" or f.mode == "active":
        scored.sort(key=lambda x: x["last_active_at"] or "", reverse=True)
    elif f.sort == "newest" or f.mode == "new":
        scored.sort(key=lambda x: x["created_at"], reverse=True)
    else:
        scored.sort(key=lambda x: (-x["rank"], x["id"]))
    if f.mode == "daily":
        scored = scored[:10]

    total = len(scored)
    fp = f.fingerprint()
    offset = _decode_cursor(f.cursor, fp)
    page = scored[offset: offset + f.limit]
    results = []
    for item in page:
        prof = public_profile(conn, viewer.id, item["id"], record_view=False)
        prof["compatibility"] = {"score": item["score"], "reasons": item["reasons"]}
        results.append(prof)
    next_offset = offset + f.limit
    return {
        "results": results,
        "total": total,
        "total_capped": len(rows) >= CANDIDATE_CAP,
        "next_cursor": _encode_cursor(next_offset, fp) if next_offset < total else None,
        "active_filters": f.active_filters(),
        "advanced_allowed": bool(ent.get("advanced_filters")),
    }


def compatibility(my_interests, their_interests, labels, my_goals, their_goals, my_langs, their_langs,
                  my_life, their_life, km, completeness) -> tuple[int, list[str]]:
    """Transparent, explainable score from stated preferences only."""
    score = 0
    reasons: list[str] = []
    shared = my_interests & their_interests
    if shared:
        score += min(30, 10 * len(shared))
        names = sorted(labels.get(i, "") for i in shared)[:3]
        reasons.append(f"{len(shared)} shared interest{'s' if len(shared) > 1 else ''}: {', '.join(names)}")
    goals = my_goals & their_goals
    if goals:
        score += 20
        reasons.append("Both looking for " + ", ".join(catalog.RELATIONSHIP_GOALS[g].lower() for g in sorted(goals)[:2]))
    langs = my_langs & their_langs
    if langs:
        score += 15
        reasons.append("You both speak " + ", ".join(catalog.LANGUAGES[lang] for lang in sorted(langs)[:2]))
    life_matches = sum(1 for a, b in zip(my_life, their_life) if a and b and a == b)
    if life_matches:
        score += 5 * life_matches
        reasons.append("Similar lifestyle choices")
    if km is not None:
        score += 10 if km < 25 else 6 if km < 100 else 2
        if km < 25:
            reasons.append("Close by")
    score += round((completeness or 0) / 10)
    return score, reasons


# ---------------------------------------------------------------------------
# Presets & saved profiles
# ---------------------------------------------------------------------------
def save_preset(conn: sqlite3.Connection, user: CurrentUser, name: str, filters: Filters) -> dict:
    name = " ".join(name.split())[:40]
    if not name:
        raise bad_request("Give your preset a name.", {"name": "Required."})
    ent = billing.entitlements(conn, user.id)
    if is_advanced(filters) and not ent.get("advanced_filters"):
        raise APIError(402, "upgrade_required", "Advanced filters are part of HeartSync Plus.")
    count = dbm.scalar(conn, "SELECT COUNT(*) FROM filter_presets WHERE user_id=?", (user.id,))
    exists = dbm.one(conn, "SELECT id FROM filter_presets WHERE user_id=? AND name=?", (user.id, name))
    if not exists and count >= ent["saved_presets"]:
        raise APIError(402, "upgrade_required", f"Your plan allows {ent['saved_presets']} saved preset(s).")
    data = filters.model_dump(exclude={"cursor", "limit"})
    with dbm.tx(conn):
        if exists:
            conn.execute("UPDATE filter_presets SET filters=? WHERE id=?", (json.dumps(data), exists["id"]))
            pid = exists["id"]
        else:
            pid = dbm.new_id()
            conn.execute("INSERT INTO filter_presets (id, user_id, name, filters, created_at) VALUES (?,?,?,?,?)",
                         (pid, user.id, name, json.dumps(data), dbm.iso()))
    return {"id": pid, "name": name, "filters": data}


def list_presets(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    return [{"id": r["id"], "name": r["name"], "filters": json.loads(r["filters"])} for r in dbm.all_(
        conn, "SELECT * FROM filter_presets WHERE user_id=? ORDER BY created_at", (user_id,))]


def delete_preset(conn: sqlite3.Connection, user_id: str, preset_id: str) -> None:
    conn.execute("DELETE FROM filter_presets WHERE id=? AND user_id=?", (preset_id, user_id))


def set_preferences(conn: sqlite3.Connection, user: CurrentUser, age_min: int, age_max: int,
                    max_distance_km: int | None, nearby_enabled: bool) -> None:
    if not (18 <= age_min <= age_max <= 100):
        raise bad_request("Choose a valid age range (18+).", {"age_min": "Invalid range."})
    if max_distance_km is not None and not (5 <= max_distance_km <= 5000):
        raise bad_request("Distance must be between 5 and 5000 km.", {"max_distance_km": "Invalid distance."})
    conn.execute(
        "INSERT INTO dating_preferences (user_id, age_min, age_max, max_distance_km, nearby_enabled, updated_at)"
        " VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET age_min=excluded.age_min, age_max=excluded.age_max,"
        " max_distance_km=excluded.max_distance_km, nearby_enabled=excluded.nearby_enabled, updated_at=excluded.updated_at",
        (user.id, age_min, age_max, max_distance_km, int(nearby_enabled), dbm.iso()),
    )


def get_preferences(conn: sqlite3.Connection, user_id: str) -> dict:
    row = dbm.one(conn, "SELECT age_min, age_max, max_distance_km, nearby_enabled FROM dating_preferences WHERE user_id=?",
                  (user_id,))
    return dict(row) if row else {"age_min": 18, "age_max": 60, "max_distance_km": None, "nearby_enabled": 0}
