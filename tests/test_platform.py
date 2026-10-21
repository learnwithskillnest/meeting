import json
import unittest

from heartsync import db as dbm
from heartsync.services.discovery import _dob_bounds
from heartsync.services.accounts import age_on, normalize_phone
from heartsync.security import password_problems
from tests.helpers import PASSWORD, AppTestCase, sql


class HeadersAndSeoTests(AppTestCase):
    def test_security_headers(self):
        r = self.anon().get("/")
        h = r.headers
        self.assertIn("default-src 'self'", h["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", h["content-security-policy"])
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertIn("referrer-policy", h)

    def test_private_pages_not_indexed(self):
        a = self.woman("w@example.com")
        for path in ("/discover", "/chat", "/settings/account", f"/u/{a.user_id}"):
            r = a.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("noindex", r.headers.get("x-robots-tag", ""), path)
            self.assertIn('name="robots" content="noindex', r.text, path)
        r = self.anon().get("/")
        self.assertNotIn("x-robots-tag", r.headers)

    def test_robots_and_sitemap(self):
        anon = self.anon()
        robots = anon.get("/robots.txt").text
        self.assertIn("Disallow: /", robots)
        sitemap = anon.get("/sitemap.xml").text
        for private in ("/discover", "/chat", "/u/", "/admin", "/api"):
            self.assertNotIn(private, sitemap)

    def test_api_errors_are_json_and_generic(self):
        r = self.anon().get("/api/does-not-exist")
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()["ok"])
        r = self.anon().c.post("/api/auth/login", content=b"{not json", headers={"X-CSRF-Token": self.anon().csrf})
        self.assertIn(r.status_code, (400, 403))

    def test_open_redirect_blocked(self):
        r = self.anon().get("/login?next=//evil.example")
        self.assertNotIn("//evil.example", r.text)

    def test_all_public_pages_render(self):
        anon = self.anon()
        for path in ("/", "/login", "/signup", "/forgot-password", "/pricing", "/help", "/safety", "/terms", "/privacy",
                     "/cookies", "/guidelines", "/refunds", "/healthz", "/brand.css"):
            self.assertEqual(anon.get(path).status_code, 200, path)

    def test_all_app_pages_render(self):
        a = self.woman("w@example.com")
        for path in ("/discover", "/search", "/likes", "/matches", "/saved", "/chat", "/profile", "/notifications",
                     "/billing", "/settings/account", "/settings/privacy", "/settings/discovery",
                     "/settings/notifications", "/settings/security", f"/u/{a.user_id}"):
            r = a.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertNotIn("Something went wrong", r.text, path)

    def test_legal_pages_are_labelled_drafts(self):
        anon = self.anon()
        for path in ("/terms", "/privacy", "/cookies", "/guidelines", "/refunds"):
            self.assertIn("qualified counsel", anon.get(path).text, path)

    def test_admin_pages_render_for_admin(self):
        a = self.woman("admin@example.com")
        self.make_staff(a)
        for path in ("/admin", "/admin/users", "/admin/reports", "/admin/verifications", "/admin/appeals",
                     "/admin/subscriptions", "/admin/audit", "/admin/content"):
            self.assertEqual(a.get(path).status_code, 200, path)

    def test_dev_outbox_unavailable_in_production_mode(self):
        self.settings().app_env = "staging"
        try:
            self.assertEqual(self.anon().get("/dev/outbox").status_code, 404)
        finally:
            self.settings().app_env = "test"


class AccountLifecycleTests(AppTestCase):
    def test_data_export_contains_only_own_data(self):
        w = self.woman("w@example.com")
        m = self.man("m@example.com")
        info = self.match(w, m)
        m.post(f"/api/conversations/{info['conversation_id']}/messages", {"body": "his words"})
        w.post(f"/api/conversations/{info['conversation_id']}/messages", {"body": "her words"})
        data = w.get("/api/account/export").json()
        blob = json.dumps(data)
        self.assertIn("her words", blob)
        self.assertNotIn("his words", blob)
        self.assertNotIn("m@example.com", blob)

    def test_account_deletion_removes_personal_data(self):
        w = self.woman("del@example.com")
        m = self.man("m@example.com")
        info = self.match(w, m)
        w.post(f"/api/conversations/{info['conversation_id']}/messages", {"body": "goodbye"})
        from heartsync.services import mfa
        secret = w.post("/api/auth/mfa/setup", {"password": PASSWORD}).json()["data"]["secret"].replace(" ", "")
        self.assertEqual(w.post("/api/auth/mfa/enable", {"code": mfa.code_at(secret, mfa.current_step())}).status_code, 200)
        self.assertEqual(w.post("/api/account/delete", {"password": "wrong", "confirm": "DELETE"}).status_code, 400)
        self.assertEqual(w.post("/api/account/delete", {"password": PASSWORD, "confirm": "nope"}).status_code, 400)
        r = w.post("/api/account/delete", {"password": PASSWORD, "confirm": "DELETE"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sql("SELECT COUNT(*) FROM users WHERE email='del@example.com'")[0][0], 0)
        self.assertEqual(sql("SELECT COUNT(*) FROM profiles WHERE user_id=?", (w.user_id,))[0][0], 0)
        self.assertEqual(sql("SELECT COUNT(*) FROM profile_photos WHERE user_id=?", (w.user_id,))[0][0], 0)
        self.assertIsNone(sql("SELECT phone_e164 FROM user_contacts WHERE user_id=?", (w.user_id,))[0][0])
        self.assertEqual(sql("SELECT COUNT(*) FROM messages WHERE body='goodbye'")[0][0], 0)
        for table in ("user_mfa", "mfa_recovery_codes", "mfa_challenges", "otp_codes", "auth_tokens"):
            self.assertEqual(sql(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (w.user_id,))[0][0], 0, table)
        self.assertEqual(w.get("/api/me").status_code, 401)
        r = self.anon().post("/api/auth/login", {"email": "del@example.com", "password": PASSWORD})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(m.get("/api/matches").json()["data"], [])

    def test_deactivate_hides_profile_and_reactivates_on_login(self):
        w = self.woman("w@example.com")
        m = self.man("m@example.com")
        w.post("/api/account/deactivate")
        self.assertNotIn(w.user_id, [p["id"] for p in m.post("/api/discover", {}).json()["data"]["results"]])
        again = self.anon()
        again.post("/api/auth/login", {"email": "w@example.com", "password": PASSWORD})
        self.assertEqual(again.get("/api/me").json()["data"]["status"], "active")

    def test_session_revoke_idor(self):
        a = self.woman("a@example.com")
        b = self.man("b@example.com")
        b_session = sql("SELECT id FROM sessions WHERE user_id=?", (b.user_id,))[0][0]
        self.assertEqual(a.delete(f"/api/auth/sessions/{b_session}").status_code, 404)
        self.assertEqual(b.get("/api/me").status_code, 200)

    def test_notifications_are_private(self):
        a = self.woman("a@example.com")
        b = self.man("b@example.com")
        self.match(a, b)
        nid = sql("SELECT id FROM notifications WHERE user_id=?", (a.user_id,))[0][0]
        b.post("/api/notifications/read", {"ids": [nid]})
        self.assertIsNone(sql("SELECT read_at FROM notifications WHERE id=?", (nid,))[0][0])
        self.assertNotIn(str(nid), json.dumps([n["id"] for n in b.get("/api/notifications").json()["data"]["items"]]))


class UnitTests(unittest.TestCase):
    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("98765 43210"), "+919876543210")
        self.assertEqual(normalize_phone("+1 (415) 555-0100"), "+14155550100")
        self.assertEqual(normalize_phone("0091 98765 43210"), "+919876543210")
        self.assertIsNone(normalize_phone("12345"))
        self.assertIsNone(normalize_phone("not a phone"))

    def test_age_and_dob_bounds(self):
        from datetime import date
        self.assertEqual(age_on("2000-02-29", date(2018, 2, 28)), 17)
        self.assertEqual(age_on("2000-02-29", date(2018, 3, 1)), 18)
        earliest, latest = _dob_bounds(25, 30)
        today = dbm.utcnow().date()
        self.assertEqual(age_on(latest, today), 25)
        self.assertEqual(age_on(earliest, today), 30)

    def test_password_policy(self):
        self.assertTrue(password_problems("short"))
        self.assertTrue(password_problems("password123"))
        self.assertTrue(password_problems("alllowercaseletters"))
        self.assertFalse(password_problems("Correct-Horse-42"))


if __name__ == "__main__":
    unittest.main()


class ProductionConfigTests(unittest.TestCase):
    BASE = dict(app_env="production", secret_key="k" * 48, database_path=":memory:", base_url="https://example.org",
                cookie_secure=True, sms_provider="twilio", twilio_account_sid="AC-placeholder",
                twilio_auth_token="placeholder", twilio_from_number="+10000000000", email_provider="smtp",
                smtp_host="smtp.example.org", payment_provider="stripe", stripe_secret_key="placeholder",
                stripe_webhook_secret="placeholder", stripe_price_plus="price_a", stripe_price_premium="price_b")

    def test_complete_production_config_boots(self):
        from heartsync.config import Settings
        s = Settings(**self.BASE)
        self.assertTrue(s.require_staff_mfa)

    def test_each_missing_setting_is_reported(self):
        from heartsync.config import Settings
        for key, bad in (("twilio_auth_token", None), ("smtp_host", None), ("stripe_webhook_secret", None),
                         ("cookie_secure", False), ("base_url", "http://example.org"), ("sms_provider", "carrier-pigeon")):
            with self.assertRaises(RuntimeError, msg=key):
                Settings(**{**self.BASE, key: bad})

    def test_payments_can_be_disabled_in_production(self):
        from heartsync.config import Settings
        Settings(**{**self.BASE, "payment_provider": "none"})


class DotenvTests(unittest.TestCase):
    def test_parsing(self):
        import os
        import tempfile
        from pathlib import Path
        from heartsync.config import _load_dotenv
        body = ("HS_T_EMPTY=                 # REQUIRED in production: 32+ chars\n"
                "HS_T_PLAIN=value   # note\n"
                "HS_T_HASH=#4a1d3f\n"
                "HS_T_QUOTED='a # b'\n"
                "HS_T_SPACES=HeartSync <no-reply@example.com>\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(body)
            keys = ["HS_T_EMPTY", "HS_T_PLAIN", "HS_T_HASH", "HS_T_QUOTED", "HS_T_SPACES"]
            for k in keys:
                os.environ.pop(k, None)
            try:
                _load_dotenv(p)
                self.assertEqual(os.environ["HS_T_EMPTY"], "")
                self.assertEqual(os.environ["HS_T_PLAIN"], "value")
                self.assertEqual(os.environ["HS_T_HASH"], "#4a1d3f")
                self.assertEqual(os.environ["HS_T_QUOTED"], "a # b")
                self.assertEqual(os.environ["HS_T_SPACES"], "HeartSync <no-reply@example.com>")
            finally:
                for k in keys:
                    os.environ.pop(k, None)

    def test_env_example_has_no_secret_values(self):
        import os
        import tempfile
        from pathlib import Path
        from heartsync.config import BASE_DIR, _load_dotenv
        text = (BASE_DIR / ".env.example").read_text()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(text)
            secret_keys = ["SECRET_KEY", "TWILIO_AUTH_TOKEN", "SMTP_PASSWORD", "STRIPE_SECRET_KEY",
                           "STRIPE_WEBHOOK_SECRET", "ANTHROPIC_API_KEY", "ADMIN_PASSWORD", "MFA_ENCRYPTION_KEY"]
            saved = {k: os.environ.pop(k, None) for k in secret_keys}
            try:
                _load_dotenv(p)
                for k in secret_keys:
                    self.assertEqual(os.environ.get(k, ""), "", k)
            finally:
                for k, v in saved.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v
