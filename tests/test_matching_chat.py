import json
import threading
import unittest


from tests.helpers import AppTestCase, jpeg_bytes, sql, sql_exec


class MatchingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.w = self.woman("w@example.com")
        self.m = self.man("m@example.com")

    def test_one_sided_like_is_not_a_match(self):
        r = self.w.post("/api/swipes", {"target_id": self.m.user_id, "kind": "like"})
        self.assertFalse(r.json()["data"]["matched"])
        self.assertEqual(self.w.get("/api/matches").json()["data"], [])

    def test_mutual_like_creates_one_match_and_conversation(self):
        info = self.match(self.w, self.m)
        self.assertEqual(sql("SELECT COUNT(*) FROM matches")[0][0], 1)
        self.assertEqual(sql("SELECT COUNT(*) FROM conversations")[0][0], 1)
        self.assertEqual(len(self.w.get("/api/matches").json()["data"]), 1)
        notes = sql("SELECT kind FROM notifications WHERE user_id=?", (self.m.user_id,))
        self.assertIn(("match",), [tuple(n) for n in notes])
        self.assertTrue(info["conversation_id"])

    def test_duplicate_likes_are_idempotent(self):
        for _ in range(3):
            r = self.w.post("/api/swipes", {"target_id": self.m.user_id, "kind": "like"})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(sql("SELECT COUNT(*) FROM swipes")[0][0], 1)
        self.m.post("/api/swipes", {"target_id": self.w.user_id, "kind": "like"})
        r = self.m.post("/api/swipes", {"target_id": self.w.user_id, "kind": "like"})
        self.assertTrue(r.json()["data"]["duplicate"])
        self.assertEqual(sql("SELECT COUNT(*) FROM matches")[0][0], 1)

    def test_concurrent_mutual_likes_create_single_match(self):
        results = []

        def like(api, target):
            results.append(api.post("/api/swipes", {"target_id": target, "kind": "like"}).status_code)

        t1 = threading.Thread(target=like, args=(self.w, self.m.user_id))
        t2 = threading.Thread(target=like, args=(self.m, self.w.user_id))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(results, [200, 200])
        self.assertEqual(sql("SELECT COUNT(*) FROM matches")[0][0], 1)
        self.assertEqual(sql("SELECT COUNT(*) FROM conversations")[0][0], 1)

    def test_match_unique_constraint_in_database(self):
        self.match(self.w, self.m)
        import sqlite3
        a, b = sorted([self.w.user_id, self.m.user_id])
        with self.assertRaises(sqlite3.IntegrityError):
            sql_exec("INSERT INTO matches (id, user_a, user_b, created_at) VALUES ('x', ?, ?, 'now')", (a, b))

    def test_cannot_like_self_or_unknown(self):
        self.assertEqual(self.w.post("/api/swipes", {"target_id": self.w.user_id, "kind": "like"}).status_code, 400)
        self.assertEqual(self.w.post("/api/swipes", {"target_id": "0" * 32, "kind": "like"}).status_code, 404)

    def test_daily_like_quota_for_free_plan(self):
        others = [self.man(f"q{i}@example.com") for i in range(3)]
        plans = self.settings().plans
        plans["free"]["daily_likes"] = 2
        try:
            codes = [self.w.post("/api/swipes", {"target_id": o.user_id, "kind": "like"}).status_code for o in others]
        finally:
            plans["free"]["daily_likes"] = 25
        self.assertEqual(codes, [200, 200, 402])
        # Passing is always allowed.
        self.assertEqual(self.w.post("/api/swipes", {"target_id": others[2].user_id, "kind": "pass"}).status_code, 200)

    def test_superlike_quota(self):
        o1, o2 = self.man("s1@example.com"), self.man("s2@example.com")
        self.assertEqual(self.w.post("/api/swipes", {"target_id": o1.user_id, "kind": "superlike"}).status_code, 200)
        self.assertEqual(self.w.post("/api/swipes", {"target_id": o2.user_id, "kind": "superlike"}).status_code, 402)

    def test_undo_requires_premium(self):
        self.w.post("/api/swipes", {"target_id": self.m.user_id, "kind": "pass"})
        self.assertEqual(self.w.post("/api/swipes/undo").status_code, 402)
        self.grant(self.w)
        self.assertEqual(self.w.post("/api/swipes/undo").status_code, 200)
        self.assertEqual(sql("SELECT COUNT(*) FROM swipes")[0][0], 0)

    def test_incoming_likes_hidden_on_free_plan(self):
        self.m.post("/api/swipes", {"target_id": self.w.user_id, "kind": "like"})
        d = self.w.get("/api/likes/incoming").json()["data"]
        self.assertEqual(d["count"], 1)
        self.assertFalse(d["visible"])
        self.assertEqual(d["profiles"], [])
        self.assertNotIn(self.m.user_id, json.dumps(d))
        self.grant(self.w)
        d = self.w.get("/api/likes/incoming").json()["data"]
        self.assertEqual([p["id"] for p in d["profiles"]], [self.m.user_id])

    def test_unmatch_closes_conversation_for_both(self):
        info = self.match(self.w, self.m)
        self.assertEqual(self.w.delete(f"/api/matches/{info['match_id']}").status_code, 200)
        for api in (self.w, self.m):
            self.assertEqual(api.get(f"/api/conversations/{info['conversation_id']}/messages").status_code, 404)
            self.assertEqual(api.post(f"/api/conversations/{info['conversation_id']}/messages", {"body": "hi"}).status_code, 404)
        # And they don't reappear in discovery.
        ids = [p["id"] for p in self.w.post("/api/discover", {}).json()["data"]["results"]]
        self.assertNotIn(self.m.user_id, ids)

    def test_third_party_cannot_unmatch(self):
        info = self.match(self.w, self.m)
        x = self.man("x@example.com")
        self.assertEqual(x.delete(f"/api/matches/{info['match_id']}").status_code, 404)


class ChatTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.w = self.woman("w@example.com")
        self.m = self.man("m@example.com")
        self.info = self.match(self.w, self.m)
        self.cid = self.info["conversation_id"]
        self.x = self.man("intruder@example.com")

    def send(self, api, body="Hello!", **kw):
        return api.post(f"/api/conversations/{self.cid}/messages", {"body": body, **kw})

    def test_exchange_messages_and_unread(self):
        r = self.send(self.w, "Hi there")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.m.get("/api/counts").json()["data"]["messages"], 1)
        msgs = self.m.get(f"/api/conversations/{self.cid}/messages").json()["data"]["messages"]
        self.assertEqual(msgs[-1]["body"], "Hi there")
        self.assertFalse(msgs[-1]["mine"])
        self.m.post(f"/api/conversations/{self.cid}/read", {"up_to_id": msgs[-1]["id"]})
        self.assertEqual(self.m.get("/api/counts").json()["data"]["messages"], 0)

    def test_outsider_cannot_read_or_send(self):
        self.send(self.w, "private")
        self.assertEqual(self.x.get(f"/api/conversations/{self.cid}/messages").status_code, 404)
        self.assertEqual(self.send(self.x, "let me in").status_code, 404)
        self.assertEqual(self.x.post(f"/api/conversations/{self.cid}/read", {"up_to_id": 999}).status_code, 404)
        mid = sql("SELECT id FROM messages LIMIT 1")[0][0]
        self.assertEqual(self.x.patch(f"/api/messages/{mid}", {"body": "hacked"}).status_code, 404)
        self.assertEqual(self.x.delete(f"/api/messages/{mid}").status_code, 404)
        self.assertEqual(self.x.post(f"/api/messages/{mid}/reactions", {"emoji": "❤️"}).status_code, 404)
        self.assertNotIn("private", json.dumps(self.x.get("/api/conversations").json()))

    def test_cannot_message_without_match(self):
        # Conversation ids only exist for matches; a guessed id is 404.
        self.assertEqual(self.x.post("/api/conversations/" + "a" * 32 + "/messages", {"body": "hi"}).status_code, 404)

    def test_blocked_user_cannot_send(self):
        self.m.post("/api/blocks", {"user_id": self.w.user_id})
        self.assertEqual(self.send(self.w, "are you there?").status_code, 404)
        self.assertEqual(self.send(self.m, "bye").status_code, 404)
        self.assertEqual(self.w.get(f"/api/conversations/{self.cid}/messages").status_code, 404)
        self.assertEqual(self.w.get("/api/conversations").json()["data"], [])
        # Unblocking doesn't restore the match.
        self.m.delete(f"/api/blocks/{self.w.user_id}")
        self.assertEqual(self.send(self.w, "hello again").status_code, 404)

    def test_message_validation(self):
        self.assertEqual(self.send(self.w, "").status_code, 400)
        self.assertEqual(self.send(self.w, "x" * 2001).status_code, 400)
        self.assertEqual(self.send(self.w, "http://a http://b http://c http://d").status_code, 400)
        # HTML is stored as text, not interpreted (rendering escapes it).
        r = self.send(self.w, "<img src=x onerror=alert(1)>")
        self.assertEqual(r.json()["data"]["body"], "<img src=x onerror=alert(1)>")

    def test_send_rate_limited(self):
        codes = [self.send(self.w, f"msg {i}").status_code for i in range(35)]
        self.assertIn(429, codes)

    def test_idempotent_client_id(self):
        r1 = self.send(self.w, "once", client_id="abc123")
        r2 = self.send(self.w, "once", client_id="abc123")
        self.assertEqual(r1.json()["data"]["id"], r2.json()["data"]["id"])
        self.assertEqual(sql("SELECT COUNT(*) FROM messages WHERE kind='text'")[0][0], 1)

    def test_reply_must_be_same_conversation(self):
        other = self.woman("w2@example.com")
        info2 = self.match(other, self.x)
        r = self.x.post(f"/api/conversations/{info2['conversation_id']}/messages", {"body": "secret"})
        foreign_id = r.json()["data"]["id"]
        self.assertEqual(self.send(self.w, "reply", reply_to_id=foreign_id).status_code, 400)

    def test_edit_and_delete_rules(self):
        mid = self.send(self.w, "typo").json()["data"]["id"]
        self.assertEqual(self.m.patch(f"/api/messages/{mid}", {"body": "not yours"}).status_code, 403)
        self.assertEqual(self.m.delete(f"/api/messages/{mid}").status_code, 403)
        r = self.w.patch(f"/api/messages/{mid}", {"body": "fixed"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["data"]["edited"])
        sql_exec("UPDATE messages SET created_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (mid,))
        self.assertEqual(self.w.patch(f"/api/messages/{mid}", {"body": "too late"}).status_code, 400)
        self.assertEqual(self.w.delete(f"/api/messages/{mid}").status_code, 200)
        msgs = self.m.get(f"/api/conversations/{self.cid}/messages").json()["data"]["messages"]
        self.assertTrue(msgs[-1]["deleted"])
        self.assertIsNone(msgs[-1]["body"])

    def test_reactions(self):
        mid = self.send(self.w, "react to me").json()["data"]["id"]
        self.assertEqual(self.m.post(f"/api/messages/{mid}/reactions", {"emoji": "💩"}).status_code, 400)
        r = self.m.post(f"/api/messages/{mid}/reactions", {"emoji": "❤️"})
        self.assertEqual(r.json()["data"], [{"user_id": self.m.user_id, "emoji": "❤️"}])
        r = self.m.post(f"/api/messages/{mid}/reactions", {"emoji": None})
        self.assertEqual(r.json()["data"], [])

    def test_image_messages_and_malicious_uploads(self):
        r = self.w.req("POST", f"/api/conversations/{self.cid}/images", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(r.status_code, 201, r.text)
        url = r.json()["data"]["attachments"][0]["url"]
        self.assertEqual(self.m.get(url).status_code, 200)
        r = self.w.req("POST", f"/api/conversations/{self.cid}/images",
                       files={"file": ("x.svg", b'<svg onload="alert(1)"></svg>', "image/svg+xml")})
        self.assertEqual(r.status_code, 400)
        r = self.w.req("POST", f"/api/conversations/{self.cid}/images",
                       files={"file": ("x.html", b"<html><script>alert(1)</script></html>", "image/jpeg")})
        self.assertEqual(r.status_code, 400)
        r = self.w.req("POST", f"/api/conversations/{self.cid}/images",
                       files={"file": ("big.jpg", b"\xff\xd8\xff" + b"A" * (9 * 1024 * 1024), "image/jpeg")})
        self.assertEqual(r.status_code, 413)
        r = self.x.req("POST", f"/api/conversations/{self.cid}/images", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(sql("SELECT COUNT(*) FROM message_attachments")[0][0], 1)

    def test_read_receipts_are_a_premium_feature(self):
        mid = self.send(self.w, "did you read this?").json()["data"]["id"]
        self.m.post(f"/api/conversations/{self.cid}/read", {"up_to_id": mid})
        msgs = self.w.get(f"/api/conversations/{self.cid}/messages").json()["data"]["messages"]
        self.assertEqual(msgs[-1]["status"], "delivered")
        self.grant(self.w)
        msgs = self.w.get(f"/api/conversations/{self.cid}/messages").json()["data"]["messages"]
        self.assertEqual(msgs[-1]["status"], "read")

    def test_message_notification_has_no_content(self):
        self.send(self.w, "my secret plans")
        rows = sql("SELECT title, body FROM notifications WHERE user_id=? AND kind='message'", (self.m.user_id,))
        self.assertTrue(rows)
        self.assertNotIn("secret", json.dumps([tuple(r) for r in rows]))

    def test_pagination(self):
        for i in range(45):
            sql_exec("INSERT INTO messages (conversation_id, sender_id, kind, body, created_at) VALUES (?,?,?,?,?)",
                     (self.cid, self.w.user_id, "text", f"m{i}", "2026-01-01T00:00:00.000000Z"))
        d = self.m.get(f"/api/conversations/{self.cid}/messages").json()["data"]
        self.assertEqual(len(d["messages"]), 30)
        self.assertTrue(d["has_more"])
        first = d["messages"][0]["id"]
        d2 = self.m.get(f"/api/conversations/{self.cid}/messages?before={first}").json()["data"]
        self.assertEqual(len(d2["messages"]), 15)
        self.assertFalse(d2["has_more"])

    def test_conversation_search_and_archive(self):
        self.send(self.w, "pizza tonight?")
        self.assertEqual(len(self.m.get("/api/conversations?q=pizza").json()["data"]), 1)
        self.assertEqual(len(self.m.get("/api/conversations?q=sushi").json()["data"]), 0)
        self.m.post(f"/api/conversations/{self.cid}/flags", {"archived": True, "muted": True})
        self.assertEqual(self.m.get("/api/conversations").json()["data"], [])
        self.assertEqual(len(self.m.get("/api/conversations?archived=1").json()["data"]), 1)


class PhonePrivacyTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.w = self.woman("w@example.com")
        self.m = self.man("m@example.com")
        self.x = self.man("x@example.com")
        self.info = self.match(self.w, self.m)
        self.mid = self.info["match_id"]
        self.w_phone = sql("SELECT phone_e164 FROM user_contacts WHERE user_id=?", (self.w.user_id,))[0][0]

    def everything_x_can_see(self):
        blobs = [self.x.post("/api/discover", {}).text, self.x.get(f"/api/profiles/{self.w.user_id}").text,
                 self.m.get(f"/api/profiles/{self.w.user_id}").text, self.m.get("/api/matches").text,
                 self.m.get("/api/conversations").text, self.m.get(f"/api/conversations/{self.info['conversation_id']}/messages").text,
                 self.m.get("/api/notifications").text]
        return "\n".join(blobs)

    def test_phone_never_exposed_without_consent(self):
        self.assertNotIn(self.w_phone, self.everything_x_can_see())
        self.assertNotIn(self.w_phone[-8:], self.everything_x_can_see())
        r = self.m.get(f"/api/matches/{self.mid}/phone/reveal")
        self.assertEqual(r.status_code, 403)

    def test_sharing_requires_explicit_confirmation(self):
        self.assertEqual(self.w.post(f"/api/matches/{self.mid}/phone/share", {"confirm": False}).status_code, 400)
        self.assertEqual(self.w.post(f"/api/matches/{self.mid}/phone/share", {}).status_code, 400)
        self.assertEqual(sql("SELECT COUNT(*) FROM phone_shares")[0][0], 0)

    def test_share_reveal_revoke(self):
        r = self.w.post(f"/api/matches/{self.mid}/phone/share", {"confirm": True})
        self.assertEqual(r.status_code, 200)
        r = self.m.get(f"/api/matches/{self.mid}/phone/reveal")
        self.assertEqual(r.json()["data"]["phone"], self.w_phone)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        # Still not in any other response.
        self.assertNotIn(self.w_phone, self.everything_x_can_see())
        # Owner cannot use the endpoint to read the other person's number.
        self.assertEqual(self.w.get(f"/api/matches/{self.mid}/phone/reveal").status_code, 403)
        # A third party can't read it, even with the match id.
        self.assertEqual(self.x.get(f"/api/matches/{self.mid}/phone/reveal").status_code, 404)
        self.w.post(f"/api/matches/{self.mid}/phone/revoke")
        self.assertEqual(self.m.get(f"/api/matches/{self.mid}/phone/reveal").status_code, 403)

    def test_unmatch_and_block_revoke_sharing(self):
        self.w.post(f"/api/matches/{self.mid}/phone/share", {"confirm": True})
        self.m.post("/api/blocks", {"user_id": self.w.user_id})
        self.assertEqual(self.m.get(f"/api/matches/{self.mid}/phone/reveal").status_code, 404)
        self.assertIsNotNone(sql("SELECT revoked_at FROM phone_shares")[0][0])

    def test_phone_not_in_logs_or_audit(self):
        self.w.post(f"/api/matches/{self.mid}/phone/share", {"confirm": True})
        self.m.get(f"/api/matches/{self.mid}/phone/reveal")
        audit = json.dumps([tuple(r) for r in sql("SELECT action, metadata FROM audit_logs")])
        self.assertNotIn(self.w_phone, audit)
        self.assertIn("phone.revealed", audit)

    def test_unverified_phone_cannot_share(self):
        sql_exec("UPDATE user_contacts SET phone_verified_at=NULL WHERE user_id=?", (self.w.user_id,))
        from heartsync import config
        config.get_settings().require_phone_verification = False
        try:
            r = self.w.post(f"/api/matches/{self.mid}/phone/share", {"confirm": True})
            self.assertEqual(r.status_code, 400)
        finally:
            config.get_settings().require_phone_verification = True


class RealtimeTests(AppTestCase):
    def test_stream_requires_auth(self):
        r = self.anon().get("/api/stream")
        self.assertEqual(r.status_code, 401)

    def test_hub_delivers_events_only_to_recipient(self):
        import asyncio
        from heartsync.realtime import Hub

        hub = Hub()

        async def run():
            qa, qb = hub.connect("a"), hub.connect("b")
            hub.publish("a", "message", {"x": 1})
            self.assertEqual(qa.qsize(), 1)
            self.assertEqual(qb.qsize(), 0)
            self.assertTrue(hub.is_online("a"))
            hub.disconnect("a", qa)
            self.assertFalse(hub.is_online("a"))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
