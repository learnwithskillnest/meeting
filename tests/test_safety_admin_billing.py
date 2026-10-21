import json
import time
import unittest

from heartsync.providers import payments
from tests.helpers import AppTestCase, sql, sql_exec


class SafetyTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.w = self.woman("w@example.com")
        self.m = self.man("m@example.com")

    def test_block_hides_both_ways(self):
        self.w.post("/api/blocks", {"user_id": self.m.user_id})
        self.assertNotIn(self.m.user_id, [p["id"] for p in self.w.post("/api/discover", {}).json()["data"]["results"]])
        self.assertNotIn(self.w.user_id, [p["id"] for p in self.m.post("/api/discover", {}).json()["data"]["results"]])
        self.assertEqual(self.m.get(f"/api/profiles/{self.w.user_id}").status_code, 404)
        self.assertEqual(self.m.post("/api/swipes", {"target_id": self.w.user_id, "kind": "like"}).status_code, 404)
        self.assertEqual(self.m.post(f"/api/saved/{self.w.user_id}").status_code, 404)

    def test_block_after_match_prevents_rematch(self):
        info = self.match(self.w, self.m)
        self.w.post("/api/blocks", {"user_id": self.m.user_id})
        self.assertEqual(self.m.get("/api/matches").json()["data"], [])
        self.assertIsNotNone(sql("SELECT unmatched_at FROM matches WHERE id=?", (info["match_id"],))[0][0])

    def test_report_profile_and_message(self):
        info = self.match(self.w, self.m)
        mid = self.m.post(f"/api/conversations/{info['conversation_id']}/messages", {"body": "send me money"}).json()["data"]["id"]
        r = self.w.post("/api/reports", {"user_id": self.m.user_id, "category": "scam", "message_id": mid,
                                         "details": "Asked for money", "block": False})
        self.assertEqual(r.status_code, 201)
        snap = json.loads(sql("SELECT evidence_snapshot FROM reports")[0][0])
        self.assertEqual(snap["body"], "send me money")
        # Evidence survives the sender deleting the message.
        self.m.delete(f"/api/messages/{mid}")
        snap = json.loads(sql("SELECT evidence_snapshot FROM reports")[0][0])
        self.assertEqual(snap["body"], "send me money")
        self.assertEqual(self.w.get("/api/reports/mine").json()["data"][0]["status"], "open")

    def test_cannot_report_message_from_foreign_conversation(self):
        x, y = self.man("x@example.com"), self.woman("y@example.com")
        info = self.match(y, x)
        mid = x.post(f"/api/conversations/{info['conversation_id']}/messages", {"body": "hi"}).json()["data"]["id"]
        r = self.w.post("/api/reports", {"user_id": x.user_id, "category": "harassment", "message_id": mid})
        self.assertEqual(r.status_code, 404)

    def test_invalid_report_category(self):
        r = self.w.post("/api/reports", {"user_id": self.m.user_id, "category": "dislike"})
        self.assertEqual(r.status_code, 400)

    def test_auto_flag_after_multiple_reporters(self):
        reporters = [self.woman(f"r{i}@example.com") for i in range(3)]
        for r in reporters:
            self.assertEqual(r.post("/api/reports", {"user_id": self.m.user_id, "category": "harassment", "block": False}).status_code, 201)
        self.assertIsNotNone(sql("SELECT flagged_at FROM users WHERE id=?", (self.m.user_id,))[0][0])
        ids = [p["id"] for p in self.w.post("/api/discover", {}).json()["data"]["results"]]
        self.assertNotIn(self.m.user_id, ids)

    def test_underage_report_flags_immediately(self):
        self.w.post("/api/reports", {"user_id": self.m.user_id, "category": "underage", "block": False})
        self.assertIsNotNone(sql("SELECT flagged_at FROM users WHERE id=?", (self.m.user_id,))[0][0])

    def test_suspended_user_is_locked_out_and_can_appeal(self):
        sql_exec("UPDATE users SET status='suspended', suspended_reason='test' WHERE id=?", (self.m.user_id,))
        r = self.m.post("/api/discover", {})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "suspended")
        self.assertEqual(self.m.post("/api/appeals", {"message": "short"}).status_code, 400)
        self.assertEqual(self.m.post("/api/appeals", {"message": "I believe this was a mistake, please review."}).status_code, 201)
        self.assertEqual(self.m.post("/api/appeals", {"message": "Another appeal while one is open..."}).status_code, 400)


class AdminAccessTests(AppTestCase):
    ADMIN_ENDPOINTS = [("GET", "/api/admin/metrics"), ("GET", "/api/admin/users"), ("GET", "/api/admin/reports"),
                       ("GET", "/api/admin/verifications"), ("GET", "/api/admin/appeals"), ("GET", "/api/admin/subscriptions"),
                       ("GET", "/api/admin/audit"), ("GET", "/api/admin/content/faq")]

    def test_unauthenticated_admin_calls(self):
        anon = self.anon()
        for method, path in self.ADMIN_ENDPOINTS:
            self.assertEqual(anon.req(method, path).status_code, 401, path)
        self.assertEqual(anon.post("/api/admin/users/abc/actions", {"action": "suspend", "reason": "x"}).status_code, 401)
        self.assertEqual(anon.c.get("/admin", follow_redirects=False).status_code, 303)

    def test_regular_user_forbidden(self):
        u = self.woman("u@example.com")
        for method, path in self.ADMIN_ENDPOINTS:
            self.assertEqual(u.req(method, path).status_code, 403, path)
        self.assertEqual(u.post(f"/api/admin/users/{u.user_id}/actions", {"action": "set_role", "role": "admin"}).status_code, 403)
        self.assertEqual(u.get("/admin").status_code, 404)

    def test_moderator_limits(self):
        mod = self.woman("mod@example.com")
        self.make_staff(mod, "moderator")
        self.assertEqual(mod.get("/api/admin/reports").status_code, 200)
        self.assertEqual(mod.get("/api/admin/audit").status_code, 403)
        self.assertEqual(mod.get("/api/admin/subscriptions").status_code, 403)
        target = self.man("t@example.com")
        self.assertEqual(mod.post(f"/api/admin/users/{target.user_id}/actions", {"action": "set_role", "role": "admin"}).status_code, 403)
        self.assertEqual(mod.post(f"/api/admin/users/{target.user_id}/actions", {"action": "grant_plan", "plan": "plus", "days": 30}).status_code, 403)

    def test_admin_moderation_flow_is_audited(self):
        admin = self.woman("admin@example.com")
        self.make_staff(admin, "admin")
        bad = self.man("bad@example.com")
        reporter = self.woman("rep@example.com")
        reporter.post("/api/reports", {"user_id": bad.user_id, "category": "harassment", "details": "rude", "block": False})
        rid = sql("SELECT id FROM reports")[0][0]
        d = admin.get(f"/api/admin/reports/{rid}").json()["data"]
        self.assertEqual(d["report"]["details"], "rude")
        r = admin.post(f"/api/admin/reports/{rid}/resolve", {"action": "suspend", "note": "Harassment confirmed"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sql("SELECT status FROM users WHERE id=?", (bad.user_id,))[0][0], "suspended")
        self.assertEqual(bad.get("/api/me").status_code, 401)  # sessions revoked
        actions = [r[0] for r in sql("SELECT action FROM audit_logs WHERE actor_id=?", (admin.user_id,))]
        self.assertIn("admin.suspend", actions)
        self.assertIn("admin.report_suspend", actions)
        self.assertIn("admin.report_viewed", actions)
        # Reinstate
        admin.post(f"/api/admin/users/{bad.user_id}/actions", {"action": "reinstate", "reason": "appeal ok"})
        self.assertEqual(sql("SELECT status FROM users WHERE id=?", (bad.user_id,))[0][0], "active")

    def test_admin_sees_masked_phone_only(self):
        admin = self.woman("admin@example.com")
        self.make_staff(admin)
        u = self.man("u@example.com")
        phone = sql("SELECT phone_e164 FROM user_contacts WHERE user_id=?", (u.user_id,))[0][0]
        d = admin.get(f"/api/admin/users/{u.user_id}").text
        self.assertNotIn(phone, d)

    def test_photo_verification_review(self):
        admin = self.woman("admin@example.com")
        self.make_staff(admin)
        u = self.man("u@example.com")
        from tests.helpers import jpeg_bytes
        r = u.req("POST", "/api/profile/verification", files={"file": ("selfie.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(r.status_code, 201)
        vid = admin.get("/api/admin/verifications").json()["data"][0]["id"]
        admin.post(f"/api/admin/verifications/{vid}", {"approve": True})
        self.assertEqual(sql("SELECT photo_verified FROM profiles WHERE user_id=?", (u.user_id,))[0][0], 1)

    def test_no_hardcoded_admin_exists(self):
        self.assertEqual(sql("SELECT COUNT(*) FROM users WHERE role!='user'")[0][0], 0)


class BillingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.u = self.woman("u@example.com")

    def entitlement(self):
        return self.u.get("/api/me").json()["data"]["entitlements"]["plan"]

    def test_mock_checkout_flow_grants_plan_via_verified_webhook(self):
        r = self.u.post("/api/billing/checkout", {"plan": "plus"})
        self.assertEqual(r.status_code, 200)
        cid = r.json()["data"]["url"].rsplit("/", 1)[1]
        self.assertEqual(self.entitlement(), "free")  # starting checkout grants nothing
        r = self.u.post(f"/api/billing/mock/{cid}/complete", {"succeed": True})
        self.assertEqual(r.json()["data"]["result"], "activated")
        self.assertEqual(self.entitlement(), "plus")
        hist = self.u.get("/api/billing").json()["data"]["history"]
        self.assertEqual(hist[0]["amount_minor"], 49900)

    def test_declined_payment_grants_nothing(self):
        cid = self.u.post("/api/billing/checkout", {"plan": "plus"}).json()["data"]["url"].rsplit("/", 1)[1]
        self.u.post(f"/api/billing/mock/{cid}/complete", {"succeed": False})
        self.assertEqual(self.entitlement(), "free")

    def test_cannot_complete_someone_elses_checkout(self):
        cid = self.u.post("/api/billing/checkout", {"plan": "plus"}).json()["data"]["url"].rsplit("/", 1)[1]
        other = self.man("o@example.com")
        self.assertEqual(other.post(f"/api/billing/mock/{cid}/complete", {"succeed": True}).status_code, 404)

    def _webhook(self, payload: dict, secret: str | None = None, ts: int | None = None):
        body = json.dumps(payload).encode()
        secret = secret if secret is not None else self.settings().mock_webhook_secret
        header = payments.build_signature_header(secret, body, ts)
        return self.anon().c.post("/api/webhooks/mock", content=body, headers={"x-mock-signature": header,
                                                                                "content-type": "application/json"})

    def test_forged_webhook_rejected(self):
        cid = self.u.post("/api/billing/checkout", {"plan": "premium"}).json()["data"]["url"].rsplit("/", 1)[1]
        evt = {"id": "evt_forged", "type": "checkout_completed", "user_id": self.u.user_id, "plan": "premium", "checkout_id": cid,
               "subscription_ref": "sub_x", "payment_ref": "pay_x", "amount_minor": 0}
        self.assertEqual(self._webhook(evt, secret="wrong-secret").status_code, 400)
        r = self.anon().c.post("/api/webhooks/mock", content=json.dumps(evt).encode())
        self.assertEqual(r.status_code, 400)  # unsigned
        self.assertEqual(self._webhook(evt, ts=int(time.time()) - 3600).status_code, 400)  # replayed/stale
        self.assertEqual(self.entitlement(), "free")

    def test_valid_webhook_is_idempotent(self):
        cid = self.u.post("/api/billing/checkout", {"plan": "plus"}).json()["data"]["url"].rsplit("/", 1)[1]
        evt = {"id": "evt_1", "type": "checkout_completed", "user_id": self.u.user_id, "plan": "plus", "checkout_id": cid,
               "subscription_ref": "sub_1", "payment_ref": "pay_1", "amount_minor": 49900, "currency": "INR"}
        self.assertEqual(self._webhook(evt).json()["data"]["result"], "activated")
        self.assertEqual(self._webhook(evt).json()["data"]["result"], "duplicate")
        self.assertEqual(sql("SELECT COUNT(*) FROM subscriptions")[0][0], 1)
        self.assertEqual(sql("SELECT COUNT(*) FROM payments")[0][0], 1)
        # Payment failure -> past_due, then cancellation event -> expired.
        self._webhook({"id": "evt_2", "type": "payment_failed", "subscription_ref": "sub_1", "payment_ref": "inv_2"})
        self.assertEqual(sql("SELECT status FROM subscriptions")[0][0], "past_due")
        self._webhook({"id": "evt_3", "type": "subscription_canceled", "subscription_ref": "sub_1"})
        self.assertEqual(self.entitlement(), "free")

    def test_webhook_cannot_upgrade_a_different_user(self):
        cid = self.u.post("/api/billing/checkout", {"plan": "plus"}).json()["data"]["url"].rsplit("/", 1)[1]
        attacker = self.man("a@example.com")
        evt = {"id": "evt_x", "type": "checkout_completed", "user_id": attacker.user_id, "plan": "plus", "checkout_id": cid}
        self.assertEqual(self._webhook(evt).json()["data"]["result"], "user_mismatch")
        self.assertEqual(attacker.get("/api/me").json()["data"]["entitlements"]["plan"], "free")

    def test_cancel_keeps_access_until_period_end(self):
        cid = self.u.post("/api/billing/checkout", {"plan": "plus"}).json()["data"]["url"].rsplit("/", 1)[1]
        self.u.post(f"/api/billing/mock/{cid}/complete", {"succeed": True})
        self.assertEqual(self.u.post("/api/billing/cancel").status_code, 200)
        self.assertEqual(self.entitlement(), "plus")
        sql_exec("UPDATE subscriptions SET current_period_end='2000-01-01T00:00:00.000000Z'")
        self.assertEqual(self.entitlement(), "free")

    def test_client_cannot_self_grant(self):
        for path, body in (("/api/billing/checkout", {"plan": "free"}), ("/api/billing/checkout", {"plan": "gold"})):
            self.assertEqual(self.u.post(path, body).status_code, 400)
        self.assertEqual(self.u.put("/api/profile", {**self.profile_payload(), "plan": "premium"}).status_code, 200)
        self.assertEqual(self.entitlement(), "free")

    def test_stripe_signature_verification(self):
        payload = b'{"id":"evt_1","type":"invoice.paid"}'
        ts = int(time.time())
        header = payments.build_signature_header("whsec_test", payload, ts)
        payments.verify_signature_header("whsec_test", payload, header)  # no exception
        with self.assertRaises(payments.WebhookRejected):
            payments.verify_signature_header("whsec_test", payload + b" ", header)
        with self.assertRaises(payments.WebhookRejected):
            payments.verify_signature_header("whsec_other", payload, header)
        with self.assertRaises(payments.WebhookRejected):
            payments.verify_signature_header("", payload, header)

    def test_stripe_webhook_rejected_when_unconfigured(self):
        r = self.anon().c.post("/api/webhooks/stripe", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"})
        self.assertEqual(r.status_code, 400)

    def test_stripe_event_normalization(self):
        evt = payments._normalize_stripe({"id": "evt", "type": "checkout.session.completed", "data": {"object": {
            "client_reference_id": "c1", "subscription": "sub", "amount_total": 49900, "currency": "inr",
            "metadata": {"user_id": "u1", "plan": "plus"}}}})
        self.assertEqual((evt.type, evt.checkout_id, evt.user_id, evt.currency), ("checkout_completed", "c1", "u1", "INR"))
        self.assertIsNone(payments._normalize_stripe({"id": "e", "type": "customer.created", "data": {"object": {}}}))


if __name__ == "__main__":
    unittest.main()
