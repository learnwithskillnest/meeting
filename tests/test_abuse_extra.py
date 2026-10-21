"""Second-layer adversarial tests: request paths a hostile client could try
that the feature tests don't exercise directly. All go through the real API."""
import json
import re
import unittest

from heartsync.providers import payments, storage
from tests.helpers import AppTestCase, jpeg_bytes, sql, sql_exec


class BlockBypassTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.w = self.woman("w@example.com")
        self.m = self.man("m@example.com")

    def test_blocked_user_cannot_view_profile_by_id(self):
        self.assertEqual(self.m.get(f"/api/profiles/{self.w.user_id}").status_code, 200)
        self.w.post("/api/blocks", {"user_id": self.m.user_id})
        # Same "not found" as a non-existent id, so the block itself isn't revealed.
        r = self.m.get(f"/api/profiles/{self.w.user_id}")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"],
                         self.m.get("/api/profiles/" + "0" * 32).json()["error"]["code"])

    def test_blocked_user_cannot_like_or_superlike(self):
        self.w.post("/api/blocks", {"user_id": self.m.user_id})
        for kind in ("like", "superlike"):
            r = self.m.post("/api/swipes", {"target_id": self.w.user_id, "kind": kind})
            self.assertGreaterEqual(r.status_code, 400, kind)
        self.assertEqual(sql("SELECT COUNT(*) FROM swipes WHERE from_user=?", (self.m.user_id,))[0][0], 0)

    def test_block_after_match_closes_conversation_both_directions(self):
        ids = self.match(self.w, self.m)
        cid = ids["conversation_id"]
        self.assertEqual(self.m.post(f"/api/conversations/{cid}/messages", {"body": "hi"}).status_code, 201)
        self.w.post("/api/blocks", {"user_id": self.m.user_id})
        for who in (self.m, self.w):
            self.assertGreaterEqual(who.post(f"/api/conversations/{cid}/messages", {"body": "again"}).status_code, 400)
            r = who.req("POST", f"/api/conversations/{cid}/images",
                        files={"file": ("x.jpg", jpeg_bytes(), "image/jpeg")})
            self.assertGreaterEqual(r.status_code, 400)
        # Blocked user can't read the history either.
        self.assertGreaterEqual(self.m.get(f"/api/conversations/{cid}/messages").status_code, 400)

    def test_unblock_does_not_resurrect_match(self):
        ids = self.match(self.w, self.m)
        self.w.post("/api/blocks", {"user_id": self.m.user_id})
        self.w.delete(f"/api/blocks/{self.m.user_id}")
        cid = ids["conversation_id"]
        self.assertGreaterEqual(self.m.post(f"/api/conversations/{cid}/messages", {"body": "back?"}).status_code, 400)


class UnmatchReuseTests(AppTestCase):
    def test_conversation_id_dead_after_unmatch(self):
        w, m = self.woman("w@example.com"), self.man("m@example.com")
        ids = self.match(w, m)
        self.assertEqual(w.delete(f"/api/matches/{ids['match_id']}").status_code, 200)
        cid = ids["conversation_id"]
        for who in (w, m):
            self.assertGreaterEqual(who.post(f"/api/conversations/{cid}/messages", {"body": "x"}).status_code, 400)
            self.assertGreaterEqual(who.post(f"/api/conversations/{cid}/typing", {}).status_code, 400)
        # And phone sharing on that match is closed.
        self.assertGreaterEqual(m.post(f"/api/matches/{ids['match_id']}/phone/share", {"confirm": True}).status_code, 400)


class MediaAccessTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.w = self.woman("w@example.com")
        self.key = sql("SELECT storage_key FROM profile_photos WHERE user_id=?", (self.w.user_id,))[0][0]

    def test_unsigned_tampered_and_expired_urls_rejected(self):
        from heartsync.security import sign
        anon = self.anon()
        self.assertIn(anon.get(f"/media/{self.key}").status_code, (403, 404))
        url = storage.signed_url(self.key)
        self.assertEqual(anon.get(url).status_code, 200)
        bad_sig = re.sub(r"s=(\w)", lambda m: "s=" + ("1" if m.group(1) != "1" else "2"), url)
        self.assertIn(anon.get(bad_sig).status_code, (403, 404))
        past = 1_000_000_000
        expired = f"/media/{self.key}?e={past}&s={sign(f'media:{self.key}:{past}')}"
        self.assertIn(anon.get(expired).status_code, (403, 404))
        # A valid signature for one key can't be reused for another.
        other = url.replace(self.key, self.key[:-5] + "x.jpg")
        self.assertIn(anon.get(other).status_code, (403, 404))

    def test_path_traversal_rejected(self):
        from heartsync.security import sign
        anon = self.anon()
        exp = "9999999999"
        for bad in ("../test.db", "..%2Ftest.db", "photos/../../test.db", "%2e%2e/test.db"):
            sig = sign(f"media:{bad}:{exp}")
            r = anon.get(f"/media/{bad}?e={exp}&s={sig}")
            self.assertIn(r.status_code, (400, 403, 404), bad)
            self.assertNotIn(b"SQLite", r.content)

    def test_chat_images_not_served_to_outsiders_via_api(self):
        m = self.man("m@example.com")
        outsider = self.man("o@example.com")
        ids = self.match(self.w, m)
        cid = ids["conversation_id"]
        r = m.req("POST", f"/api/conversations/{cid}/images", files={"file": ("x.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(outsider.get(f"/api/conversations/{cid}/messages").status_code, 404)


class DiscoveryBypassTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.woman("me@example.com")
        self.adult = self.man("adult@example.com", dob="1990-01-01")

    def test_age_filter_cannot_go_below_18(self):
        r = self.me.post("/api/discover", {"age_min": 13, "age_max": 17})
        self.assertEqual(r.status_code, 400)

    def test_unknown_filter_keys_have_no_effect(self):
        sql_exec("UPDATE profiles SET visibility='hidden' WHERE user_id=?", (self.adult.user_id,))
        for extra in ({"include_hidden": True}, {"visibility": "hidden"}, {"phone": "+91"}, {"status": "suspended"}):
            r = self.me.post("/api/discover", extra)
            self.assertIn(r.status_code, (200, 400))
            if r.status_code == 200:
                self.assertEqual(r.json()["data"]["results"], [], extra)

    def test_hidden_and_deactivated_never_returned_even_by_id(self):
        sql_exec("UPDATE profiles SET visibility='hidden' WHERE user_id=?", (self.adult.user_id,))
        d = self.me.post("/api/discover", {}).json()["data"]
        self.assertNotIn(self.adult.user_id, [p["id"] for p in d["results"]])
        self.assertEqual(self.me.get(f"/api/profiles/{self.adult.user_id}").status_code, 404)

    def test_oversized_limit_clamped_or_rejected(self):
        r = self.me.post("/api/discover", {"limit": 100000})
        self.assertIn(r.status_code, (200, 400))
        if r.status_code == 200:
            self.assertLessEqual(len(r.json()["data"]["results"]), 50)


class BillingAbuseTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.u = self.woman("u@example.com")

    def _webhook(self, evt: dict, secret=None):
        s = self.settings()
        payload = json.dumps(evt).encode()
        sig = payments.build_signature_header(secret or s.mock_webhook_secret, payload)
        return self.anon().c.post("/api/webhooks/mock", content=payload,
                                  headers={"Content-Type": "application/json", "X-Mock-Signature": sig})

    def test_replayed_webhook_with_old_timestamp_rejected(self):
        s = self.settings()
        payload = json.dumps({"id": "evt_old", "type": "checkout_completed"}).encode()
        sig = payments.build_signature_header(s.mock_webhook_secret, payload, timestamp=1_000_000_000)
        r = self.anon().c.post("/api/webhooks/mock", content=payload,
                               headers={"Content-Type": "application/json", "X-Mock-Signature": sig})
        self.assertEqual(r.status_code, 400)

    def test_premium_endpoints_enforced_server_side(self):
        # Free user calls premium APIs directly.
        liker = self.man("liker@example.com")
        liker.post("/api/swipes", {"target_id": self.u.user_id, "kind": "like"})
        d = self.u.get("/api/likes/incoming").json()["data"]
        self.assertEqual((d["count"], d["visible"], d["profiles"]), (1, False, []))
        self.assertNotIn(liker.user_id, json.dumps(d))
        self.assertEqual(self.u.post("/api/swipes/undo", {}).status_code, 402)
        self.assertEqual(self.u.post("/api/discover", {"smoking": ["never"]}).status_code, 402)
        self.assertEqual(self.u.post("/api/boost", {}).status_code, 402)

    def test_expired_subscription_loses_entitlements(self):
        self.grant(self.u)
        self.assertTrue(self.u.get("/api/likes/incoming").json()["data"]["visible"])
        sql_exec("UPDATE subscriptions SET current_period_end='2000-01-01T00:00:00.000000Z' WHERE user_id=?",
                 (self.u.user_id,))
        self.assertFalse(self.u.get("/api/likes/incoming").json()["data"]["visible"])
        self.assertEqual(self.u.post("/api/swipes/undo", {}).status_code, 402)


class AdminEscalationTests(AppTestCase):
    def test_moderator_cannot_reach_admin_only_apis(self):
        mod = self.woman("mod@example.com")
        self.make_staff(mod, "moderator")
        self.assertEqual(mod.get("/api/admin/audit").status_code, 403)
        self.assertEqual(mod.get("/api/admin/subscriptions").status_code, 403)

    def test_user_cannot_change_own_role_via_profile(self):
        u = self.woman("u@example.com")
        r = u.put("/api/profile", self.profile_payload(role="admin"))
        self.assertIn(r.status_code, (200, 400))
        self.assertEqual(sql("SELECT role FROM users WHERE id=?", (u.user_id,))[0][0], "user")


if __name__ == "__main__":
    unittest.main()


class DecompressionBombTests(AppTestCase):
    def test_huge_dimension_image_rejected_before_decoding(self):
        import io
        import struct
        import zlib
        from heartsync.providers import storage
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

        def fake_png(w, h):  # header claims w x h; body is tiny
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0)) +
                    chunk(b"IDAT", zlib.compress(b"\x00" * 1000)) + chunk(b"IEND", b""))
        # 49 MP: above our cap but below Pillow's hard error (80 MP), which used to be decoded.
        # 100 MP: Pillow's own guard. Both refused with the same clear message.
        for w, h in ((7000, 7000), (10000, 10000)):
            png = fake_png(w, h)
            self.assertLess(len(png), 10_000)
            with self.assertRaisesRegex(storage.UploadRejected, "dimensions are too large"):
                storage.process_image(png)
        u = self.woman("u@example.com")
        r = u.req("POST", "/api/profile/photos", files={"file": ("bomb.png", io.BytesIO(fake_png(7000, 7000)), "image/png")})
        self.assertEqual(r.status_code, 400)
