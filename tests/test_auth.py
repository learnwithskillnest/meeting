import re
import unittest

from heartsync import db as dbm
from heartsync import security
from tests.helpers import PASSWORD, AppTestCase, outbox_last, sql, sql_exec


class SignupTests(AppTestCase):
    def test_rejects_minors_and_weak_passwords(self):
        a = self.anon()
        r = a.post("/api/auth/signup", {"email": "kid@example.com", "password": "short", "date_of_birth": "2012-01-01",
                                       "accept_terms": True, "confirm_adult": True})
        self.assertEqual(r.status_code, 400)
        fields = r.json()["error"]["fields"]
        self.assertIn("date_of_birth", fields)
        self.assertIn("password", fields)
        self.assertEqual(sql("SELECT COUNT(*) FROM users")[0][0], 0)

    def test_age_boundary_exactly_18_today(self):
        today = dbm.utcnow().date()
        try:
            dob = today.replace(year=today.year - 18)
        except ValueError:
            dob = today.replace(year=today.year - 18, day=28)
        a = self.anon()
        r = a.post("/api/auth/signup", {"email": "eighteen@example.com", "password": PASSWORD,
                                       "date_of_birth": dob.isoformat(), "accept_terms": True, "confirm_adult": True})
        self.assertEqual(r.status_code, 201, r.text)

    def test_requires_terms_and_adult_confirmation(self):
        r = self.anon().post("/api/auth/signup", {"email": "x@example.com", "password": PASSWORD,
                                                 "date_of_birth": "1990-01-01", "accept_terms": False, "confirm_adult": True})
        self.assertEqual(r.status_code, 400)

    def test_duplicate_email(self):
        self.signup("dup@example.com")
        r = self.anon().post("/api/auth/signup", {"email": "DUP@example.com", "password": PASSWORD,
                                                 "date_of_birth": "1990-01-01", "accept_terms": True, "confirm_adult": True})
        self.assertEqual(r.status_code, 409)

    def test_password_is_hashed(self):
        self.signup("hash@example.com")
        stored = sql("SELECT password_hash FROM users WHERE email='hash@example.com'")[0][0]
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn(PASSWORD, stored)
        self.assertTrue(security.verify_password(PASSWORD, stored))


class CsrfTests(AppTestCase):
    def test_mutations_require_csrf_token(self):
        a = self.anon()
        r = a.req("POST", "/api/auth/login", json={"email": "a@b.co", "password": "x"}, csrf=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "csrf_failed")

    def test_cross_origin_blocked(self):
        a = self.anon()
        r = a.req("POST", "/api/auth/login", json={"email": "a@b.co", "password": "x"},
                  headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)


class LoginTests(AppTestCase):
    def test_login_logout_and_session_revocation(self):
        self.signup("login@example.com")
        a = self.anon()
        r = a.post("/api/auth/login", {"email": "login@example.com", "password": "wrong-password-1"})
        self.assertEqual(r.status_code, 401)
        r = a.post("/api/auth/login", {"email": "login@example.com", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(a.get("/api/me").status_code, 200)
        b = self.anon()
        b.post("/api/auth/login", {"email": "login@example.com", "password": PASSWORD})
        # Logging out other devices from b revokes a.
        r = b.post("/api/auth/logout-others")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(a.get("/api/me").status_code, 401)
        self.assertEqual(b.get("/api/me").status_code, 200)
        b.post("/api/auth/logout")
        self.assertEqual(b.get("/api/me").status_code, 401)

    def test_unknown_email_same_error_as_wrong_password(self):
        self.signup("known@example.com")
        a = self.anon()
        r1 = a.post("/api/auth/login", {"email": "known@example.com", "password": "nope-nope-1"})
        r2 = a.post("/api/auth/login", {"email": "unknown@example.com", "password": "nope-nope-1"})
        self.assertEqual(r1.status_code, r2.status_code)
        self.assertEqual(r1.json()["error"], r2.json()["error"])

    def test_login_rate_limited(self):
        self.signup("brute@example.com")
        a = self.anon()
        codes = [a.post("/api/auth/login", {"email": "brute@example.com", "password": f"guess-{i}-Aa"}).status_code
                 for i in range(12)]
        self.assertIn(429, codes)

    def test_session_cookie_flags(self):
        a = self.anon()
        r = a.post("/api/auth/signup", {"email": "cookie@example.com", "password": PASSWORD, "date_of_birth": "1990-01-01",
                                       "accept_terms": True, "confirm_adult": True})
        cookie = r.headers.get("set-cookie", "")
        self.assertIn("hs_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        token = a.c.cookies.get("hs_session")
        # Only a keyed hash of the token is stored.
        self.assertEqual(sql("SELECT COUNT(*) FROM sessions WHERE token_hash=?", (token,))[0][0], 0)


class EmailAndResetTests(AppTestCase):
    def test_email_verification_link(self):
        a = self.signup("verify@example.com")
        me = a.get("/api/me").json()["data"]
        self.assertFalse(me["email_verified"])
        self.assertEqual(me["next"], "/verify-email")
        self.verify_email(a)
        self.assertTrue(a.get("/api/me").json()["data"]["email_verified"])

    def test_password_reset_flow_and_single_use(self):
        self.signup("reset@example.com")
        anon = self.anon()
        r = anon.post("/api/auth/password/forgot", {"email": "reset@example.com"})
        r_unknown = anon.post("/api/auth/password/forgot", {"email": "nobody@example.com"})
        self.assertEqual(r.json(), r_unknown.json())  # no account enumeration
        token = re.search(r"token=([\w-]+)", outbox_last("email", "reset@example.com")).group(1)
        r = anon.post("/api/auth/password/reset", {"token": token, "password": "New-Password-99"})
        self.assertEqual(r.status_code, 200, r.text)
        r = anon.post("/api/auth/password/reset", {"token": token, "password": "Another-Pass-77"})
        self.assertEqual(r.status_code, 400)
        r = anon.post("/api/auth/login", {"email": "reset@example.com", "password": "New-Password-99"})
        self.assertEqual(r.status_code, 200)

    def test_expired_reset_token(self):
        self.signup("exp@example.com")
        anon = self.anon()
        anon.post("/api/auth/password/forgot", {"email": "exp@example.com"})
        token = re.search(r"token=([\w-]+)", outbox_last("email", "exp@example.com")).group(1)
        sql_exec("UPDATE auth_tokens SET expires_at=? WHERE purpose='reset_password'", (dbm.iso_in(minutes=-1),))
        r = anon.post("/api/auth/password/reset", {"token": token, "password": "New-Password-99"})
        self.assertEqual(r.status_code, 400)


class OtpTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.signup("otp@example.com")
        self.verify_email(self.a)

    def test_invalid_otp(self):
        self.a.post("/api/phone/send-otp", {"phone": "+919876500001"})
        r = self.a.post("/api/phone/verify", {"code": "000000"})
        code = re.search(r"\b(\d{6})\b", outbox_last("sms", "+919876500001")).group(1)
        if code == "000000":  # astronomically unlikely
            return
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "invalid_otp")

    def test_expired_otp(self):
        self.a.post("/api/phone/send-otp", {"phone": "+919876500002"})
        code = re.search(r"\b(\d{6})\b", outbox_last("sms", "+919876500002")).group(1)
        sql_exec("UPDATE otp_codes SET expires_at=?", (dbm.iso_in(minutes=-1),))
        r = self.a.post("/api/phone/verify", {"code": code})
        self.assertEqual(r.status_code, 400)

    def test_otp_attempt_limit_burns_code(self):
        self.a.post("/api/phone/send-otp", {"phone": "+919876500003"})
        code = re.search(r"\b(\d{6})\b", outbox_last("sms", "+919876500003")).group(1)
        wrong = "111111" if code != "111111" else "222222"
        for _ in range(5):
            self.a.post("/api/phone/verify", {"code": wrong})
        r = self.a.post("/api/phone/verify", {"code": code})
        self.assertEqual(r.status_code, 400)  # correct code no longer accepted
        self.assertEqual(sql("SELECT phone_verified_at FROM user_contacts WHERE user_id=?", (self.a.user_id,))[0][0], None)

    def test_number_owned_by_someone_else_is_not_revealed(self):
        other = self.signup("owner@example.com")
        self.verify_email(other)
        self.verify_phone(other, "+919876500004")
        r = self.a.post("/api/phone/send-otp", {"phone": "+919876500004"})
        # Same response shape as a fresh number - doesn't reveal registration.
        self.assertEqual(r.status_code, 200)
        self.assertIn("masked", r.json()["data"])
        # No usable code was issued to the requester.
        self.assertEqual(sql("SELECT COUNT(*) FROM otp_codes WHERE user_id=? AND consumed_at IS NULL", (self.a.user_id,))[0][0], 0)

    def test_otp_send_rate_limited(self):
        codes = [self.a.post("/api/phone/send-otp", {"phone": "+919876500005"}).status_code for _ in range(6)]
        self.assertIn(429, codes)

    def test_invalid_phone_rejected(self):
        r = self.a.post("/api/phone/send-otp", {"phone": "12345"})
        self.assertEqual(r.status_code, 400)

    def test_otp_codes_are_hashed(self):
        self.a.post("/api/phone/send-otp", {"phone": "+919876500006"})
        code = re.search(r"\b(\d{6})\b", outbox_last("sms", "+919876500006")).group(1)
        stored = sql("SELECT code_hash FROM otp_codes WHERE user_id=?", (self.a.user_id,))[0][0]
        self.assertNotIn(code, stored)


class UnverifiedAccessTests(AppTestCase):
    def test_unverified_user_cannot_use_dating_features(self):
        a = self.signup("unverified@example.com")
        target = self.man("target@example.com")
        for method, path, body in [("POST", "/api/discover", {}), ("POST", "/api/swipes", {"target_id": target.user_id, "kind": "like"}),
                                   ("GET", "/api/matches", None), ("GET", "/api/conversations", None),
                                   ("GET", f"/api/profiles/{target.user_id}", None)]:
            r = a.req(method, path, json=body)
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json()["error"]["code"], "verification_required")
        # Email verified but phone not -> still blocked.
        self.verify_email(a)
        self.assertEqual(a.post("/api/discover", {}).status_code, 403)
        # Phone verified but no profile -> still blocked.
        self.verify_phone(a)
        self.assertEqual(a.post("/api/discover", {}).status_code, 403)

    def test_unauthenticated_requests(self):
        a = self.anon()
        self.assertEqual(a.post("/api/discover", {}).status_code, 401)
        self.assertEqual(a.get("/api/me").status_code, 401)
        r = a.c.get("/discover", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login"))


if __name__ == "__main__":
    unittest.main()
