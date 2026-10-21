"""Profile-writing suggestions (rules provider and AI fallback)."""
import os
import unittest
from unittest import mock

from heartsync.providers import ai
from tests.helpers import AppTestCase, sql


class AssistTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.u = self.woman("u@example.com", about="Hi.", profession="Architect", religion="jain",
                            field_visibility={"religion": "hidden"})

    def test_tips_point_at_missing_things(self):
        r = self.u.post("/api/profile/assist", {"kind": "tips"})
        self.assertEqual(r.status_code, 200, r.text)
        keys = {t["key"] for t in r.json()["data"]["tips"]}
        self.assertIn("about", keys)   # bio is 3 characters
        self.assertIn("photos", keys)  # only one photo

    def test_bio_draft_is_a_labelled_suggestion_and_not_saved(self):
        before = sql("SELECT about FROM profiles WHERE user_id=?", (self.u.user_id,))[0][0]
        for tone in ("warm", "playful", "direct"):
            d = self.u.post("/api/profile/assist", {"kind": "bio", "tone": tone}).json()["data"]
            self.assertEqual(d["source"], "rules")
            self.assertTrue(d["label"].startswith("Suggestion"))
            self.assertIn("Asha", d["draft"])
            self.assertIn("Lucknow", d["draft"])
            self.assertLessEqual(len(d["draft"]), 1200)
        self.assertEqual(sql("SELECT about FROM profiles WHERE user_id=?", (self.u.user_id,))[0][0], before)

    def test_draft_never_uses_sensitive_fields(self):
        d = self.u.post("/api/profile/assist", {"kind": "bio"}).json()["data"]
        self.assertNotIn("jain", d["draft"].lower())

    def test_validation_and_auth(self):
        self.assertEqual(self.u.post("/api/profile/assist", {"kind": "write_my_messages"}).status_code, 400)
        self.assertEqual(self.anon().post("/api/profile/assist", {"kind": "tips"}).status_code, 401)

    def test_ai_provider_failure_falls_back_to_rules(self):
        self.settings().ai_provider = "anthropic"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder-not-a-real-key"}), \
                mock.patch("urllib.request.urlopen", side_effect=TimeoutError):
            self.assertEqual(ai.provider_name(), "anthropic")
            d = self.u.post("/api/profile/assist", {"kind": "bio"}).json()["data"]
        self.assertEqual(d["source"], "rules")

    def test_ai_output_with_contact_details_is_discarded(self):
        self.settings().ai_provider = "anthropic"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder-not-a-real-key"}), \
                mock.patch.object(ai, "complete", return_value="Call me on 98765 43210 or insta @asha"):
            d = self.u.post("/api/profile/assist", {"kind": "bio"}).json()["data"]
        self.assertEqual(d["source"], "rules")
        self.assertNotIn("98765", d["draft"])

    def test_ai_output_is_labelled(self):
        self.settings().ai_provider = "anthropic"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "placeholder-not-a-real-key"}), \
                mock.patch.object(ai, "complete", return_value="I design buildings and love long walks. What's yours?"):
            d = self.u.post("/api/profile/assist", {"kind": "bio"}).json()["data"]
        self.assertEqual(d["source"], "anthropic")
        self.assertIn("AI", d["label"])

    def test_without_key_provider_is_rules(self):
        self.settings().ai_provider = "anthropic"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertEqual(ai.provider_name(), "rules")


if __name__ == "__main__":
    unittest.main()
