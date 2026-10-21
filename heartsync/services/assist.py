"""Profile-writing help: quality tips and an optional "About me" draft.

Everything returned is a labelled suggestion. The member decides whether to
copy any of it into their profile; nothing is saved here. Drafts are passed
through the same contact-detail filter as real bios.
"""
from __future__ import annotations

import random
import sqlite3

from .. import catalog
from ..providers import ai
from ..web import CurrentUser
from . import profiles

TONES = ("warm", "playful", "direct")
# Only these fields are ever used to write a draft. Religion, family plans,
# relationship status and other sensitive fields are deliberately excluded.
DRAFT_FIELDS = ("display_name", "city", "profession", "industry", "interests", "relationship_goals",
                "travel_interests", "exercise", "diet", "pets", "communication_style")


def _labels(slugs: list[str], mapping: dict) -> list[str]:
    return [mapping.get(s, s.replace("_", " ")) for s in slugs or []]


def _interest_labels(conn: sqlite3.Connection, slugs: list[str]) -> list[str]:
    if not slugs:
        return []
    rows = conn.execute(
        f"SELECT slug, label FROM interests WHERE slug IN ({','.join('?' * len(slugs))})", slugs).fetchall()
    by_slug = {r["slug"]: r["label"] for r in rows}
    return [by_slug.get(s, s) for s in slugs]


def tips(conn: sqlite3.Connection, user: CurrentUser) -> list[dict]:
    p = profiles.own_profile(conn, user.id)
    out: list[dict] = []

    def add(key, text, field=None):
        out.append({"key": key, "text": text, "field": field})

    photos = len(p.get("photos") or [])
    if photos < 3:
        add("photos", f"Add {3 - photos} more photo{'s' if 3 - photos != 1 else ''}. Profiles with three or more "
                      "clear, recent photos give people a better sense of you.", "photos")
    about = (p.get("about") or "").strip()
    if len(about) < 80:
        add("about", "Your “About me” is short. Two or three sentences about what you enjoy and what you're "
                     "looking for give people something to reply to.", "about")
    elif "?" not in about:
        add("question", "End your “About me” with a question — it makes it easier for someone to start a chat.",
            "about")
    if not p.get("headline"):
        add("headline", "Add a headline — a short line that captures you, like “Sunrise hikes and second breakfasts”.",
            "headline")
    if len(p.get("interests") or []) < 4:
        add("interests", "Pick a few more interests. Shared interests are the main way we explain why someone "
                         "might be a good fit.", "interests")
    if not p.get("relationship_goals"):
        add("goals", "Say what you're looking for. People can then tell quickly whether you want the same thing.",
            "relationship_goals")
    if not p.get("languages"):
        add("languages", "Add the languages you speak.", "languages")
    if not p.get("photo_verified"):
        add("verify", "Request photo verification to show a verified badge. A moderator reviews each request.",
            "verification")
    return out[:6]


def _rules_draft(info: dict, tone: str) -> str:
    rnd = random.Random()
    interests = info["interests"][:3]
    city = info.get("city")
    job = info.get("profession")
    goals = info["relationship_goals"]
    travel = info["travel_interests"][:1]

    opener = {
        "warm": ["Hi, I'm {name}.", "Hello! I'm {name}."],
        "playful": ["Hey, {name} here!", "Hi, I'm {name}!"],
        "direct": ["I'm {name}.", "{name}."],
    }[tone]
    parts = [rnd.choice(opener).format(name=info.get("display_name") or "new here")]
    if job and city:
        parts.append({"warm": f"I work as a {job.lower()} and call {city} home.",
                      "playful": f"By day I'm a {job.lower()} in {city}.",
                      "direct": f"{job} based in {city}."}[tone])
    elif city:
        parts.append(f"Based in {city}.")
    if interests:
        joined = interests[0].lower() if len(interests) == 1 else \
            ", ".join(i.lower() for i in interests[:-1]) + " and " + interests[-1].lower()
        parts.append({"warm": f"I'm happiest spending time on {joined}.",
                      "playful": f"Ask me about {joined} at your own risk — I will talk for hours.",
                      "direct": f"Into {joined}."}[tone])
    if travel:
        parts.append(f"Next on my travel list: {travel[0].lower()}.")
    if goals:
        g = goals[0].lower()
        parts.append({"warm": f"I'm hoping to find {('a ' + g) if not g.endswith('s') else g} with someone kind and curious.",
                      "playful": f"Looking for {g} — and someone who laughs at their own jokes.",
                      "direct": f"Looking for: {g}."}[tone])
    parts.append({"warm": "What's something small that made you smile this week?",
                  "playful": "Tell me your most controversial food opinion.",
                  "direct": "Say hi and tell me about your week."}[tone])
    return " ".join(parts)


def bio_draft(conn: sqlite3.Connection, user: CurrentUser, tone: str) -> dict:
    tone = tone if tone in TONES else "warm"
    p = profiles.own_profile(conn, user.id)
    info = {k: p.get(k) for k in DRAFT_FIELDS}
    info["interests"] = _interest_labels(conn, p.get("interests") or [])
    info["relationship_goals"] = _labels(p.get("relationship_goals") or [], catalog.RELATIONSHIP_GOALS)
    info["travel_interests"] = _labels(p.get("travel_interests") or [], catalog.TRAVEL)

    source = "rules"
    text = None
    if ai.provider_name() != "rules":
        facts = {k: v for k, v in info.items() if v}
        text = ai.complete(
            system=("You help people write a short, honest dating-profile 'About me'. Use only the facts given. "
                    "Do not invent jobs, places, achievements or personality traits. No contact details, no emojis, "
                    "no clichés about 'partners in crime'. 3-4 sentences, first person, end with a light question."),
            prompt=f"Tone: {tone}.\nFacts (JSON): {facts}",
            max_tokens=300,
        )
        if text:
            source = ai.provider_name()
    if not text:
        text = _rules_draft(info, tone)
    text = text.strip()[:1200]
    if profiles._looks_like_contact(text):  # never suggest anything the bio filter would reject
        text = _rules_draft(info, tone)
        source = "rules"
    return {"draft": text, "tone": tone, "source": source,
            "label": "Suggestion" + (" written with AI" if source != "rules" else " from your profile details")}
