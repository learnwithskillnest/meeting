"""Reference data (interests, default help content). Idempotent."""
from __future__ import annotations

import re
import sqlite3

from . import catalog
from . import db as dbm

DEFAULT_FAQ = """## Getting started

**Who can join?** Adults aged 18 and over. We confirm your email and mobile number, and you can optionally verify your photos.

**Why do you need my phone number?** To reduce fake accounts. Your number is never shown on your profile. You can choose to share it with one specific match, and you can stop sharing at any time.

## Matching

**How do matches work?** When two people like each other, it's a match and you can chat.

**What does the compatibility score mean?** It's a simple count of things you have in common based on what you both chose to share — interests, relationship goals, languages, lifestyle and distance. It is not a personality test or a prediction.

**Why can't I see who liked me?** Free members see how many people liked them. HeartSync Plus shows who they are.

## Privacy & safety

**Who can see my profile details?** You decide per field: everyone, only matches, or no one. Sensitive fields like religion and family plans default to matches only.

**How do I block or report someone?** Open their profile or your chat with them and choose Report or Block. Blocking immediately ends the match and hides you from each other.

## Billing

**How do I cancel?** Go to Billing and choose Cancel. You keep premium features until the end of the period you paid for, and you won't be charged again.

**Do you offer refunds?** See our Refund Policy in the Terms. Contact support if you were charged in error.
"""

DEFAULT_SAFETY = """## Before you meet

- Keep conversations on HeartSync until you feel comfortable. Share your number only when you're ready — you can revoke it later.
- Never send money, gift cards or crypto to someone you haven't met, whatever the story.
- Video-call before meeting in person.

## Meeting in person

- Meet in a busy public place and arrange your own transport.
- Tell a friend where you're going and when you'll check in.
- Stay sober enough to make good decisions, and keep your drink with you.

## If something feels wrong

- Leave. You don't owe anyone an explanation.
- Block and report — reports are confidential, and the other person is not told who reported them.
- In an emergency in India, call **112**. Women's helpline: **181**. Cyber crime: **1930** or cybercrime.gov.in.
"""


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def ensure_reference_data(conn: sqlite3.Connection) -> None:
    with dbm.tx(conn):
        for category, labels in catalog.INTERESTS.items():
            for label in labels:
                conn.execute("INSERT OR IGNORE INTO interests (slug, label, category) VALUES (?,?,?)",
                             (slugify(label), label, category))
        for slug, title, body in (("faq", "Help & FAQ", DEFAULT_FAQ), ("safety-tips", "Safety tips", DEFAULT_SAFETY)):
            conn.execute("INSERT OR IGNORE INTO content_pages (slug, title, body, updated_at) VALUES (?,?,?,?)",
                         (slug, title, body, dbm.iso()))
