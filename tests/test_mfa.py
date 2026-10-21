"""Two-factor sign-in (TOTP) through the real API."""
import time
import unittest

from heartsync.services import mfa
from tests.helpers import PASSWORD, AppTestCase, sql


def _code(secret_grouped: str, offset_steps: int = 0) -> str:
    secret = secret_grouped.replace(" ", "")
    return mfa.code_at(secret, mfa.current_step() + offset_steps)


class TotpPrimitiveTests(unittest.TestCase):
    def test_rfc6238_reference_vector(self):
        # RFC 6238 appendix B, SHA-1 secret "12345678901234567890", T=59 -> 94287082 (8 digits).
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(mfa.code_at(secret, 59 // 30), "287082")  # last 6 digits

    def test_drift_window_and_replay(self):
        secret = mfa.new_secret()
        now = time.time()
        step = mfa.current_step(now)
        self.assertEqual(mfa.matching_step(secret, mfa.code_at(secret, step - 1), 0, now), step - 1)
        self.assertIsNone(mfa.matching_step(secret, mfa.code_at(secret, step - 3), 0, now))
        self.assertIsNone(mfa.matching_step(secret, mfa.code_at(secret, step), step, now))  # already used
        self.assertIsNone(mfa.matching_step(secret, "abcdef", 0, now))


class MfaFlowTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.u = self.woman("u@example.com")

    def enrol(self):
        r = self.u.post("/api/auth/mfa/setup", {"password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        secret = r.json()["data"]["secret"]
        self.assertIn("otpauth://totp/", r.json()["data"]["otpauth_uri"])
        r = self.u.post("/api/auth/mfa/enable", {"code": _code(secret)})
        self.assertEqual(r.status_code, 200, r.text)
        return secret, r.json()["data"]["recovery_codes"]

    def login(self, client=None):
        a = client or self.anon()
        r = a.post("/api/auth/login", {"email": "u@example.com", "password": PASSWORD})
        return a, r

    def test_setup_requires_password_and_valid_code(self):
        self.assertEqual(self.u.post("/api/auth/mfa/setup", {"password": "wrong-password-1"}).status_code, 400)
        r = self.u.post("/api/auth/mfa/setup", {"password": PASSWORD})
        self.assertEqual(self.u.post("/api/auth/mfa/enable", {"code": "000000"}).status_code, 400)
        self.assertFalse(self.u.get("/api/auth/mfa").json()["data"]["enabled"])
        self.assertEqual(r.status_code, 200)

    def test_secret_encrypted_at_rest_and_recovery_codes_hashed(self):
        secret, codes = self.enrol()
        stored = sql("SELECT secret_enc FROM user_mfa")[0][0]
        self.assertNotIn(secret.replace(" ", ""), stored)
        hashes = [r[0] for r in sql("SELECT code_hash FROM mfa_recovery_codes")]
        self.assertEqual(len(hashes), 10)
        for c in codes:
            self.assertNotIn(c, hashes)

    def test_login_requires_second_factor(self):
        secret, _ = self.enrol()
        a, r = self.login()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["data"]["mfa_required"])
        self.assertIsNone(a.c.cookies.get("hs_session"))
        # Password alone gives no access.
        self.assertEqual(a.get("/api/me").status_code, 401)
        # A code that was already used at enrolment is rejected (replay).
        time.sleep(0)  # same step most likely; previous-step code also already consumed
        r = a.post("/api/auth/mfa/verify", {"code": _code(secret, -1)})
        self.assertEqual(r.status_code, 400)
        r = a.post("/api/auth/mfa/verify", {"code": _code(secret, +1)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(a.get("/api/me").status_code, 200)
        # The same code can't be used again on a second sign-in.
        b, _ = self.login()
        self.assertEqual(b.post("/api/auth/mfa/verify", {"code": _code(secret, +1)}).status_code, 400)

    def test_challenge_burns_after_five_wrong_codes(self):
        secret, _ = self.enrol()
        a, _ = self.login()
        for _ in range(4):
            self.assertEqual(a.post("/api/auth/mfa/verify", {"code": "111111"}).status_code, 400)
        self.assertEqual(a.post("/api/auth/mfa/verify", {"code": "111111"}).status_code, 401)
        # Even a correct code no longer works on the burned challenge.
        self.assertEqual(a.post("/api/auth/mfa/verify", {"code": _code(secret, +1)}).status_code, 401)
        self.assertEqual(a.get("/api/me").status_code, 401)

    def test_verify_without_challenge_cookie(self):
        secret, _ = self.enrol()
        a = self.anon()
        self.assertEqual(a.post("/api/auth/mfa/verify", {"code": _code(secret, +1)}).status_code, 401)

    def test_expired_challenge(self):
        secret, _ = self.enrol()
        a, _ = self.login()
        from tests.helpers import sql_exec
        sql_exec("UPDATE mfa_challenges SET expires_at='2000-01-01T00:00:00.000000Z'")
        self.assertEqual(a.post("/api/auth/mfa/verify", {"code": _code(secret, +1)}).status_code, 401)

    def test_recovery_code_single_use(self):
        _, codes = self.enrol()
        a, _ = self.login()
        self.assertEqual(a.post("/api/auth/mfa/verify", {"code": codes[0].upper()}).status_code, 200)
        b, _ = self.login()
        self.assertEqual(b.post("/api/auth/mfa/verify", {"code": codes[0]}).status_code, 400)
        self.assertEqual(self.u.get("/api/auth/mfa").json()["data"]["recovery_codes_remaining"], 9)

    def test_enabling_signs_out_other_devices(self):
        other = self.anon()
        other.post("/api/auth/login", {"email": "u@example.com", "password": PASSWORD})
        self.assertEqual(other.get("/api/me").status_code, 200)
        self.enrol()
        self.assertEqual(other.get("/api/me").status_code, 401)
        self.assertEqual(self.u.get("/api/me").status_code, 200)

    def test_disable_requires_password_and_code(self):
        secret, codes = self.enrol()
        self.assertEqual(self.u.post("/api/auth/mfa/disable", {"password": PASSWORD, "code": "000000"}).status_code, 400)
        self.assertEqual(self.u.post("/api/auth/mfa/disable",
                                     {"password": "Wrong-pass-123", "code": codes[1]}).status_code, 400)
        self.assertEqual(self.u.post("/api/auth/mfa/disable", {"password": PASSWORD, "code": codes[1]}).status_code, 200)
        a, r = self.login()
        self.assertNotIn("mfa_required", r.json()["data"] or {})
        self.assertEqual(a.get("/api/me").status_code, 200)

    def test_password_reset_does_not_bypass_mfa(self):
        self.enrol()
        from tests.helpers import outbox_last
        import re
        a = self.anon()
        a.post("/api/auth/password/forgot", {"email": "u@example.com"})
        token = re.search(r"token=([\w-]+)", outbox_last("email", "u@example.com")).group(1)
        r = a.post("/api/auth/password/reset", {"token": token, "password": "Brand-new-pass-77"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(a.c.cookies.get("hs_session"))
        r = a.post("/api/auth/login", {"email": "u@example.com", "password": "Brand-new-pass-77"})
        self.assertTrue(r.json()["data"]["mfa_required"])

    def test_mfa_endpoints_require_auth(self):
        a = self.anon()
        for path in ("/api/auth/mfa/setup", "/api/auth/mfa/enable", "/api/auth/mfa/disable",
                     "/api/auth/mfa/recovery-codes"):
            self.assertEqual(a.post(path, {"password": "x", "code": "1"}).status_code, 401, path)


if __name__ == "__main__":
    unittest.main()
