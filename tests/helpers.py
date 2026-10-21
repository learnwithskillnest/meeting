"""Test harness: isolated database per test and helpers that drive the real
HTTP API (no frontend mocks)."""
from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import unittest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-1234567890")
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "heartsync-test-bootstrap.db"))

from PIL import Image  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from heartsync import config  # noqa: E402
from heartsync import db as dbm  # noqa: E402
from heartsync.main import app  # noqa: E402
from heartsync.security import rate_limiter  # noqa: E402

import logging  # noqa: E402

logging.disable(logging.WARNING)  # keep test output readable; errors still show

PASSWORD = "Correct-Horse-42"


def jpeg_bytes(size=(64, 80), color=(200, 80, 120), exif_gps=False) -> bytes:
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    if exif_gps:
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (26.0, 51.0, 0.0), 3: "E", 4: (80.0, 57.0, 0.0)}  # GPSInfo
        exif[0x010F] = "SpyCam"  # Make
        img.save(out, "JPEG", exif=exif)
    else:
        img.save(out, "JPEG")
    return out.getvalue()


class Api:
    """A logged-in (or anonymous) browser-like client with CSRF handling."""

    def __init__(self, client: TestClient):
        self.c = client
        self.user_id: str | None = None
        self.email: str | None = None
        if not self.c.cookies.get("hs_csrf"):
            self.c.get("/healthz")

    @property
    def csrf(self) -> str:
        return self.c.cookies.get("hs_csrf") or ""

    def req(self, method: str, path: str, json=None, files=None, headers=None, csrf=True):
        h = dict(headers or {})
        if csrf and method != "GET":
            h["X-CSRF-Token"] = self.csrf
        return self.c.request(method, path, json=json, files=files, headers=h)

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, json=None, **kw):
        return self.req("POST", path, json=json if json is not None else {}, **kw)

    def put(self, path, json=None, **kw):
        return self.req("PUT", path, json=json, **kw)

    def delete(self, path, **kw):
        return self.req("DELETE", path, **kw)

    def patch(self, path, json=None, **kw):
        return self.req("PATCH", path, json=json, **kw)


def outbox_last(channel: str, recipient: str) -> str:
    conn = dbm.connect()
    try:
        row = dbm.one(conn, "SELECT body FROM dev_outbox WHERE channel=? AND recipient=? ORDER BY id DESC LIMIT 1",
                      (channel, recipient))
        return row["body"] if row else ""
    finally:
        conn.close()


def sql(query: str, params=()):
    conn = dbm.connect()
    try:
        cur = conn.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


class AppTestCase(unittest.TestCase):
    """Fresh database and media directory for every test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hs-test-")
        config.override_settings(
            app_env="test", secret_key=os.environ["SECRET_KEY"],
            database_path=os.path.join(self.tmp, "test.db"), media_root=os.path.join(self.tmp, "media"),
        )
        rate_limiter.reset()
        self._root_client = TestClient(app)
        self._root_client.__enter__()  # runs lifespan: migrations + reference data
        self._phone_counter = 0

    def tearDown(self):
        self._root_client.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def anon(self) -> Api:
        return Api(TestClient(app))

    def settings(self):
        return config.get_settings()

    def signup(self, email: str, dob: str = "1995-06-15") -> Api:
        rate_limiter.reset()  # many accounts share the test client's IP
        a = self.anon()
        r = a.post("/api/auth/signup", {"email": email, "password": PASSWORD, "date_of_birth": dob,
                                       "accept_terms": True, "confirm_adult": True})
        self.assertEqual(r.status_code, 201, r.text)
        a.email = email
        a.user_id = sql("SELECT id FROM users WHERE email=?", (email,))[0][0]
        return a

    def verify_email(self, a: Api) -> None:
        body = outbox_last("email", a.email)
        token = re.search(r"token=([\w-]+)", body).group(1)
        r = a.get(f"/verify-email/confirm?token={token}")
        self.assertIn(r.status_code, (200,), r.text[:200])

    def verify_phone(self, a: Api, phone: str | None = None) -> str:
        self._phone_counter += 1
        phone = phone or f"+9198765{self._phone_counter:05d}"
        r = a.post("/api/phone/send-otp", {"phone": phone})
        self.assertEqual(r.status_code, 200, r.text)
        code = re.search(r"\b(\d{6})\b", outbox_last("sms", phone)).group(1)
        r = a.post("/api/phone/verify", {"code": code})
        self.assertEqual(r.status_code, 200, r.text)
        return phone

    def profile_payload(self, **over) -> dict:
        p = {
            "display_name": "Asha", "gender": "woman", "interested_in": ["man"], "country": "IN",
            "region": "Uttar Pradesh", "city": "Lucknow", "languages": ["en", "hi"], "headline": "Hello there",
            "about": "I enjoy long walks and good books.", "interests": ["reading", "yoga", "coffee"],
            "relationship_goals": ["long_term"], "height_cm": 165, "smoking": "never", "drinking": "socially",
            "diet": "vegetarian", "religion": "hindu", "education": "masters",
        }
        p.update(over)
        return p

    def member(self, email: str, dob: str = "1995-06-15", **profile) -> Api:
        """Fully verified, onboarded member created through the public API."""
        a = self.signup(email, dob)
        self.verify_email(a)
        self.verify_phone(a)
        r = a.req("POST", "/api/profile/photos", files={"file": ("me.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(r.status_code, 201, r.text)
        r = a.post("/api/profile/complete", self.profile_payload(**profile))
        self.assertEqual(r.status_code, 200, r.text)
        return a

    def man(self, email: str, **kw) -> Api:
        kw.setdefault("display_name", "Ravi")
        kw.setdefault("gender", "man")
        kw.setdefault("interested_in", ["woman"])
        return self.member(email, **kw)

    def woman(self, email: str, **kw) -> Api:
        kw.setdefault("display_name", "Asha")
        return self.member(email, **kw)

    def match(self, a: Api, b: Api) -> dict:
        r1 = a.post("/api/swipes", {"target_id": b.user_id, "kind": "like"})
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = b.post("/api/swipes", {"target_id": a.user_id, "kind": "like"})
        self.assertEqual(r2.status_code, 200, r2.text)
        data = r2.json()["data"]
        self.assertTrue(data["matched"])
        conv = sql("SELECT id FROM conversations WHERE match_id=?", (data["match_id"],))[0][0]
        return {"match_id": data["match_id"], "conversation_id": conv}

    def make_staff(self, a: Api, role: str = "admin") -> None:
        sql_exec("UPDATE users SET role=? WHERE id=?", (role, a.user_id))

    def grant(self, a: Api, plan: str = "plus") -> None:
        sql_exec("INSERT INTO subscriptions (id, user_id, plan, status, provider, provider_subscription_id,"
                 " current_period_end, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (dbm.new_id(), a.user_id, plan, "active", "test", "t-" + dbm.new_id(), dbm.iso_in(days=30),
                  dbm.iso(), dbm.iso()))


def sql_exec(query: str, params=()):
    conn = dbm.connect()
    try:
        conn.execute(query, params)
    finally:
        conn.close()
