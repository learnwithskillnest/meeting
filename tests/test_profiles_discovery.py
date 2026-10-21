import io
import json
import time
import unittest

from PIL import Image

from heartsync.providers import storage
from tests.helpers import AppTestCase, jpeg_bytes, sql, sql_exec

FORBIDDEN_KEYS = {"phone", "phone_e164", "email", "date_of_birth", "lat_approx", "lng_approx", "password_hash"}


def walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v)


class ProfileTests(AppTestCase):
    def test_contact_details_rejected_in_bio(self):
        a = self.woman("w@example.com")
        for about in ("Call me on 98765 43210", "mail me at me@example.com", "insta: @someone"):
            r = a.put("/api/profile", self.profile_payload(about=about))
            self.assertEqual(r.status_code, 400, about)

    def test_invalid_enum_and_city_rejected(self):
        a = self.woman("w@example.com")
        self.assertEqual(a.put("/api/profile", self.profile_payload(smoking="chain")).status_code, 400)
        self.assertEqual(a.put("/api/profile", self.profile_payload(city="Atlantis")).status_code, 400)
        self.assertEqual(a.put("/api/profile", self.profile_payload(display_name="<script>")).status_code, 400)

    def test_onboarding_requires_a_photo(self):
        a = self.signup("nophoto@example.com")
        self.verify_email(a)
        self.verify_phone(a)
        r = a.post("/api/profile/complete", self.profile_payload())
        self.assertEqual(r.status_code, 400)
        self.assertIn("photos", r.json()["error"]["fields"])

    def test_field_visibility_matches_only(self):
        w = self.woman("w@example.com", religion="hindu", field_visibility={"religion": "matches", "height_cm": "hidden"})
        m = self.man("m@example.com")
        prof = m.get(f"/api/profiles/{w.user_id}").json()["data"]
        self.assertIsNone(prof["religion"])
        self.assertIsNone(prof["height_cm"])
        self.match(w, m)
        prof = m.get(f"/api/profiles/{w.user_id}").json()["data"]
        self.assertEqual(prof["religion"], "hindu")
        self.assertIsNone(prof["height_cm"])  # hidden from everyone
        # Owner always sees own data.
        own = w.get("/api/profile").json()["data"]
        self.assertEqual(own["height_cm"], 165)

    def test_public_profile_never_contains_private_fields(self):
        w = self.woman("w@example.com")
        m = self.man("m@example.com")
        prof = m.get(f"/api/profiles/{w.user_id}").json()["data"]
        self.assertFalse(FORBIDDEN_KEYS & set(walk_keys(prof)))
        self.assertNotIn("98765", json.dumps(prof))

    def test_photo_upload_strips_exif_and_rejects_bad_files(self):
        a = self.woman("w@example.com")
        r = a.req("POST", "/api/profile/photos", files={"file": ("gps.jpg", jpeg_bytes(exif_gps=True), "image/jpeg")})
        self.assertEqual(r.status_code, 201, r.text)
        key = sql("SELECT storage_key FROM profile_photos WHERE id=?", (r.json()["data"]["id"],))[0][0]
        with Image.open(io.BytesIO(storage.read(key))) as img:
            exif = img.getexif()
            self.assertNotIn(0x8825, exif)  # GPS removed
            self.assertNotIn(0x010F, exif)
        # Disguised script
        r = a.req("POST", "/api/profile/photos", files={"file": ("evil.jpg", b"<?php system($_GET['c']); ?>", "image/jpeg")})
        self.assertEqual(r.status_code, 400)
        # Polyglot: valid GIF header but not an allowed format
        gif = io.BytesIO()
        Image.new("RGB", (10, 10)).save(gif, "GIF")
        r = a.req("POST", "/api/profile/photos", files={"file": ("x.jpg", gif.getvalue(), "image/jpeg")})
        self.assertEqual(r.status_code, 400)
        # Oversized
        r = a.req("POST", "/api/profile/photos", files={"file": ("big.jpg", b"\xff\xd8" + b"0" * (9 * 1024 * 1024), "image/jpeg")})
        self.assertEqual(r.status_code, 413)

    def test_photo_limit_and_ownership(self):
        a = self.woman("w@example.com")
        b = self.man("m@example.com")
        for _ in range(5):
            a.req("POST", "/api/profile/photos", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")})
        r = a.req("POST", "/api/profile/photos", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(r.status_code, 400)  # max 6
        pid = sql("SELECT id FROM profile_photos WHERE user_id=? LIMIT 1", (a.user_id,))[0][0]
        self.assertEqual(b.delete(f"/api/profile/photos/{pid}").status_code, 404)  # IDOR
        self.assertEqual(a.delete(f"/api/profile/photos/{pid}").status_code, 200)

    def test_signed_media_urls(self):
        a = self.woman("w@example.com")
        url = a.get("/api/profile").json()["data"]["photos"][0]["url"]
        anon = self.anon()
        self.assertEqual(anon.get(url).status_code, 200)
        self.assertEqual(anon.get(url.replace("&s=", "&s=0")).status_code, 403)  # tampered
        key = url.split("?")[0][len("/media/"):]
        self.assertEqual(anon.get(f"/media/{key}").status_code, 403)  # unsigned
        past = int(time.time()) - 10
        from heartsync.security import sign
        self.assertEqual(anon.get(f"/media/{key}?e={past}&s={sign(f'media:{key}:{past}')}").status_code, 403)  # expired
        self.assertIn(anon.get("/media/../../etc/passwd?e=9999999999&s=x").status_code, (403, 404))
        self.assertEqual(anon.get("/media/photos/%2e%2e/%2e%2e/test.db?e=9999999999&s=x").status_code, 403)

    def test_hidden_profile_not_in_discovery(self):
        w = self.woman("w@example.com")
        m = self.man("m@example.com")
        w.post("/api/profile/visibility", {"visible": False})
        ids = [p["id"] for p in m.post("/api/discover", {}).json()["data"]["results"]]
        self.assertNotIn(w.user_id, ids)
        self.assertEqual(m.get(f"/api/profiles/{w.user_id}").status_code, 404)


class DiscoveryTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.woman("me@example.com", interests=["reading", "yoga"])
        self.a = self.man("a@example.com", dob="1993-01-01", height_cm=180, smoking="never", interests=["reading", "yoga"],
                          region="Uttar Pradesh", city="Lucknow", relationship_goals=["long_term"], religion="sikh",
                          field_visibility={"religion": "everyone"})
        self.b = self.man("b@example.com", dob="1985-01-01", height_cm=170, smoking="regularly", interests=["gaming"],
                          region="Maharashtra", city="Mumbai", relationship_goals=["casual"], religion="sikh")
        self.c = self.man("c@example.com", dob="2000-01-01", height_cm=None, smoking=None, interests=["reading"],
                          region="Uttar Pradesh", city="Kanpur", relationship_goals=["long_term"])
        self.w2 = self.woman("w2@example.com")  # not interested in women -> never shown to me

    def ids(self, **filters):
        r = self.me.post("/api/discover", filters)
        self.assertEqual(r.status_code, 200, r.text)
        return {p["id"] for p in r.json()["data"]["results"]}

    def test_reciprocal_gender_preferences(self):
        ids = self.ids()
        self.assertEqual(ids, {self.a.user_id, self.b.user_id, self.c.user_id})
        self.assertNotIn(self.w2.user_id, ids)
        self.assertNotIn(self.me.user_id, ids)

    def test_reciprocal_age_preferences(self):
        sql_exec("UPDATE dating_preferences SET age_min=40, age_max=50 WHERE user_id=?", (self.a.user_id,))
        self.assertNotIn(self.a.user_id, self.ids())

    def test_age_filter(self):
        ids = self.ids(age_min=29, age_max=35)
        self.assertEqual(ids, {self.a.user_id})

    def test_location_filters(self):
        self.assertEqual(self.ids(region="Uttar Pradesh"), {self.a.user_id, self.c.user_id})
        self.assertEqual(self.ids(city="Mumbai"), {self.b.user_id})
        self.assertEqual(self.ids(max_distance_km=100), {self.a.user_id, self.c.user_id})  # Kanpur ~70km
        self.assertEqual(self.ids(max_distance_km=25), {self.a.user_id})

    def test_goals_filter(self):
        self.assertEqual(self.ids(goals=["long_term"]), {self.a.user_id, self.c.user_id})

    def test_advanced_filters_require_plan(self):
        r = self.me.post("/api/discover", {"smoking": ["never"]})
        self.assertEqual(r.status_code, 402)
        self.grant(self.me)
        self.assertEqual(self.ids(smoking=["never"]), {self.a.user_id})

    def test_strict_vs_flexible(self):
        self.grant(self.me)
        self.assertEqual(self.ids(height_min=175, height_max=190), {self.a.user_id})
        self.assertEqual(self.ids(height_min=175, height_max=190, strict=False), {self.a.user_id, self.c.user_id})
        self.assertEqual(self.ids(smoking=["never"], strict=False), {self.a.user_id, self.c.user_id})

    def test_exclusions_and_interests(self):
        self.grant(self.me)
        self.assertEqual(self.ids(exclude={"smoking": ["regularly"]}), {self.a.user_id, self.c.user_id})
        self.assertEqual(self.ids(interests=["reading"]), {self.a.user_id, self.c.user_id})
        self.assertEqual(self.ids(interests=["reading", "yoga"], interests_mode="all"), {self.a.user_id})
        self.assertEqual(self.ids(common_interests=True), {self.a.user_id, self.c.user_id})

    def test_sensitive_filter_only_matches_public_values(self):
        self.grant(self.me)
        # b's religion is "matches only" (default) so a religion filter must not reveal it.
        self.assertEqual(self.ids(religion=["sikh"]), {self.a.user_id})

    def test_no_results_returns_empty_not_error(self):
        r = self.me.post("/api/discover", {"age_min": 90, "age_max": 99})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["total"], 0)
        self.assertEqual(r.json()["data"]["results"], [])

    def test_compatibility_explains_reasons(self):
        r = self.me.post("/api/discover", {}).json()["data"]["results"]
        a = next(p for p in r if p["id"] == self.a.user_id)
        self.assertTrue(any("shared interest" in x for x in a["compatibility"]["reasons"]))
        self.assertGreater(a["compatibility"]["score"], 0)

    def test_pagination_with_cursor(self):
        for i in range(5):
            self.man(f"extra{i}@example.com")
        seen, cursor = [], None
        while True:
            body = {"limit": 3}
            if cursor:
                body["cursor"] = cursor
            d = self.me.post("/api/discover", body).json()["data"]
            seen += [p["id"] for p in d["results"]]
            cursor = d["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 8)
        # Cursor bound to filters: reusing it with different filters fails safely.
        d = self.me.post("/api/discover", {"limit": 3}).json()["data"]
        r = self.me.post("/api/discover", {"limit": 3, "cursor": d["next_cursor"], "age_min": 20})
        self.assertEqual(r.status_code, 400)

    def test_results_never_leak_private_fields(self):
        d = self.me.post("/api/discover", {}).json()["data"]
        self.assertFalse(FORBIDDEN_KEYS & set(walk_keys(d)))

    def test_excludes_swiped_blocked_suspended(self):
        self.me.post("/api/swipes", {"target_id": self.a.user_id, "kind": "pass"})
        self.b.post("/api/blocks", {"user_id": self.me.user_id})  # b blocks me
        sql_exec("UPDATE users SET status='suspended' WHERE id=?", (self.c.user_id,))
        self.assertEqual(self.ids(), set())

    def test_presets_and_plan_limits(self):
        r = self.me.post("/api/presets", {"name": "Lucknow", "filters": {"city": "Lucknow"}})
        self.assertEqual(r.status_code, 201)
        r = self.me.post("/api/presets", {"name": "Second", "filters": {"city": "Mumbai"}})
        self.assertEqual(r.status_code, 402)  # free plan: 1 preset
        presets = self.me.get("/api/presets").json()["data"]
        self.assertEqual(presets[0]["filters"]["city"], "Lucknow")
        other = self.a
        self.assertEqual(len(other.get("/api/presets").json()["data"]), 0)

    def test_nearby_requires_opt_in(self):
        r = self.me.post("/api/discover", {"mode": "nearby"})
        self.assertEqual(r.status_code, 400)
        self.me.put("/api/preferences", {"age_min": 18, "age_max": 60, "nearby_enabled": True})
        r = self.me.post("/api/discover", {"mode": "nearby"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual({p["id"] for p in r.json()["data"]["results"]}, {self.a.user_id, self.c.user_id})

    def test_injection_attempts_are_inert(self):
        self.grant(self.me)
        r = self.me.post("/api/discover", {"profession": "' OR 1=1 --"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["total"], 0)
        r = self.me.post("/api/discover", {"exclude": {"smoking = 1; DROP TABLE users; --": ["never"]}})
        self.assertEqual(r.status_code, 400)
        self.assertGreater(sql("SELECT COUNT(*) FROM users")[0][0], 0)


if __name__ == "__main__":
    unittest.main()
