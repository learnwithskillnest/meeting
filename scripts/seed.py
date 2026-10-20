#!/usr/bin/env python3
"""Seed a development database with fictional members.

    SEED_PASSWORD='choose-a-dev-password1' python scripts/seed.py [--count 40]

All people are invented. Photos are generated abstract illustrations (no real
faces). Refuses to run when APP_ENV=production.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from heartsync import catalog  # noqa: E402
from heartsync import db as dbm  # noqa: E402
from heartsync import security  # noqa: E402
from heartsync.config import get_settings  # noqa: E402
from heartsync.providers import storage  # noqa: E402
from heartsync.seed_data import ensure_reference_data  # noqa: E402
from heartsync.services.profiles import refresh_completeness  # noqa: E402

FIRST = {
    "woman": ["Aanya", "Ishita", "Meera", "Sana", "Kavya", "Riya", "Tara", "Nisha", "Zoya", "Priya", "Diya", "Anika", "Leela", "Mira", "Pooja"],
    "man": ["Arjun", "Kabir", "Rohan", "Vihaan", "Aditya", "Imran", "Karan", "Dev", "Rahul", "Neel", "Sameer", "Yash", "Aarav", "Farhan", "Vikram"],
    "nonbinary": ["Ari", "Sam", "Noor", "Kiran", "Rumi"],
}
HEADLINES = ["Chai first, decisions later", "Weekend trekker, weekday problem-solver", "Looking for my favourite person",
             "Will cook for good conversation", "Bookshop wanderer", "Trying every momo in the city",
             "Plant parent, aspiring runner", "Old songs, new places", "Here for something real"]
ABOUTS = ["I work in {ind} and spend weekends exploring {city}'s food scene. Looking for someone kind, curious and up for spontaneous road trips.",
          "Big believer in long walks, longer conversations and terrible puns. I'm close to my family and looking for something meaningful.",
          "Recently moved to {city}. I love {i1} and {i2}, and I'm always up for trying a new café. Tell me your best travel story!",
          "Introvert with extrovert moments. Into {i1}, {i2} and quiet Sunday mornings. Honesty matters more to me than anything."]
PALETTES = [((122, 58, 104), (217, 87, 122)), ((74, 29, 63), (240, 184, 160)), ((63, 122, 92), (233, 196, 106)),
            ((47, 111, 222), (217, 87, 122)), ((168, 100, 26), (122, 58, 104)), ((43, 15, 37), (236, 122, 153))]


def avatar(initial: str, seed: int) -> bytes:
    rnd = random.Random(seed)
    a, b = rnd.choice(PALETTES)
    w, h = 600, 800
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        px.line([(0, y), (w, y)], fill=tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3)))
    for _ in range(6):
        r = rnd.randint(60, 220)
        x, y = rnd.randint(-50, w), rnd.randint(-50, h)
        px.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255), width=3)
    try:
        font = ImageFont.load_default(size=260)
    except TypeError:
        font = ImageFont.load_default()
    px.text((w / 2, h / 2), initial, fill=(255, 255, 255), font=font, anchor="mm")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return out.getvalue()


def make_user(conn, email, password, dob, gender, interested, name, rnd, city, verified_phone=True, role="user"):
    uid, now = dbm.new_id(), dbm.iso()
    created = dbm.iso_in(days=-rnd.randint(0, 120))
    conn.execute("INSERT INTO users (id, email, password_hash, date_of_birth, role, email_verified_at, terms_accepted_at,"
                 " created_at, updated_at, last_active_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (uid, email, password, dob, role, now, now, created, now, dbm.iso_in(hours=-rnd.randint(0, 24 * 20))))
    phone = f"+9199{rnd.randint(10000000, 99999999)}" if verified_phone else None
    conn.execute("INSERT INTO user_contacts (user_id, phone_e164, phone_verified_at, updated_at) VALUES (?,?,?,?)",
                 (uid, phone, now if verified_phone else None, now))
    conn.execute("INSERT INTO dating_preferences (user_id, age_min, age_max, updated_at) VALUES (?,?,?,?)",
                 (uid, 21, 45, now))
    (country, region, cname), (lat, lng) = city
    interests = rnd.sample([r[0] for r in dbm.all_(conn, "SELECT slug FROM interests")], rnd.randint(4, 9))
    labels = {r[0]: r[1] for r in dbm.all_(conn, "SELECT slug, label FROM interests")}
    ind = rnd.choice(list(catalog.INDUSTRIES))
    profile = {
        "user_id": uid, "display_name": name, "gender": gender, "interested_in": json.dumps(interested),
        "headline": rnd.choice(HEADLINES),
        "about": rnd.choice(ABOUTS).format(ind=catalog.INDUSTRIES[ind].lower(), city=cname,
                                           i1=labels[interests[0]].lower(), i2=labels[interests[1]].lower()),
        "country": country, "region": region, "city": cname, "lat_approx": round(lat, 1), "lng_approx": round(lng, 1),
        "languages": json.dumps(sorted(set(["en", rnd.choice(["hi", "hi", "bn", "ta", "mr", "pa", "gu"])]))),
        "height_cm": rnd.randint(152, 188), "education": rnd.choice(list(catalog.EDUCATION)),
        "profession": rnd.choice(["Designer", "Engineer", "Doctor", "Teacher", "Analyst", "Writer", "Architect", "Chef", "Founder"]),
        "industry": ind, "work_status": rnd.choice(["employed", "employed", "self_employed", "student"]),
        "religion": rnd.choice([None, *catalog.RELIGION]), "relationship_status": "single",
        "smoking": rnd.choice(["never", "never", "socially", "regularly"]), "drinking": rnd.choice(list(catalog.DRINKING)),
        "diet": rnd.choice(list(catalog.DIET)), "exercise": rnd.choice(list(catalog.EXERCISE)), "pets": rnd.choice(list(catalog.PETS)),
        "sleep_habits": rnd.choice(list(catalog.SLEEP)),
        "travel_interests": json.dumps(rnd.sample(list(catalog.TRAVEL), 2)),
        "relationship_goals": json.dumps(rnd.sample(["long_term", "marriage", "friendship", "exploring", "casual", "companionship"], rnd.randint(1, 2))),
        "partner_qualities": json.dumps(rnd.sample(list(catalog.PARTNER_QUALITIES), 3)),
        "communication_style": rnd.choice(list(catalog.COMMUNICATION)), "family_plans": rnd.choice(list(catalog.FAMILY_PLANS)),
        "open_to_relocate": rnd.choice([0, 1, None]), "field_visibility": json.dumps(catalog.VISIBILITY_FIELDS),
        "photo_verified": int(rnd.random() < 0.4), "onboarded_at": now, "created_at": now, "updated_at": now,
    }
    cols = list(profile)
    conn.execute(f"INSERT INTO profiles ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", profile)
    for slug in interests:
        conn.execute("INSERT INTO profile_interests (user_id, interest_id) SELECT ?, id FROM interests WHERE slug=?", (uid, slug))
    for i in range(rnd.randint(1, 3)):
        stored = storage.save_image(f"photos/{uid}", avatar(name[0], hash((uid, i)) & 0xFFFF))
        conn.execute("INSERT INTO profile_photos (id, user_id, storage_key, position, width, height, created_at) VALUES (?,?,?,?,?,?,?)",
                     (dbm.new_id(), uid, stored.key, i, stored.width, stored.height, now))
    refresh_completeness(conn, uid)
    return uid


def main() -> int:
    s = get_settings()
    if s.is_production:
        print("Refusing to seed fictional data in production.", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=40)
    args = ap.parse_args()
    password = os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(12) + "1!"
    pw_hash = security.hash_password(password)
    rnd = random.Random(42)
    conn = dbm.connect()
    dbm.migrate(conn)
    ensure_reference_data(conn)
    if dbm.scalar(conn, "SELECT COUNT(*) FROM users WHERE email LIKE '%@heartsync.test'"):
        print("Seed data already present (emails ending @heartsync.test). Delete var/heartsync.db to reseed.")
        return 0
    cities = list(catalog.CITIES.items())
    lucknowish = [c for c in cities if c[0][1] in ("Uttar Pradesh", "Delhi")]
    with dbm.tx(conn):
        demo = make_user(conn, "demo@heartsync.test", pw_hash, "1996-05-14", "woman", ["man"], "Asha", rnd,
                         (("IN", "Uttar Pradesh", "Lucknow"), catalog.CITIES[("IN", "Uttar Pradesh", "Lucknow")]))
        demo2 = make_user(conn, "demo2@heartsync.test", pw_hash, "1994-09-02", "man", ["woman"], "Vikram", rnd,
                          (("IN", "Uttar Pradesh", "Lucknow"), catalog.CITIES[("IN", "Uttar Pradesh", "Lucknow")]))
        ids = []
        for i in range(args.count):
            gender = rnd.choice(["woman", "man", "man", "woman", "nonbinary"])
            interested = {"woman": ["man"], "man": ["woman"], "nonbinary": ["woman", "man", "nonbinary"]}[gender]
            if rnd.random() < 0.15:
                interested = sorted(set(interested + ["woman", "man"]))
            year = rnd.randint(1984, 2004)
            dob = f"{year}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
            city = rnd.choice(lucknowish if rnd.random() < 0.5 else cities)
            ids.append(make_user(conn, f"member{i + 1}@heartsync.test", pw_hash, dob, gender, interested,
                                 rnd.choice(FIRST[gender]), rnd, city))
        # Some members already like the demo accounts, so Likes and matching can be tried immediately.
        for uid in ids[:12]:
            g = dbm.scalar(conn, "SELECT gender FROM profiles WHERE user_id=?", (uid,))
            target = demo if g == "man" else demo2 if g == "woman" else None
            if target:
                conn.execute("INSERT OR IGNORE INTO swipes (from_user, to_user, kind, created_at) VALUES (?,?,?,?)",
                             (uid, target, rnd.choice(["like", "like", "superlike"]), dbm.iso()))
        # Demo accounts like each other -> an existing match with a conversation.
        from heartsync.services.matching import _create_match

        conn.execute("INSERT INTO swipes (from_user, to_user, kind, created_at) VALUES (?,?,?,?)", (demo, demo2, "like", dbm.iso()))
        conn.execute("INSERT INTO swipes (from_user, to_user, kind, created_at) VALUES (?,?,?,?)", (demo2, demo, "like", dbm.iso()))
        mid = _create_match(conn, demo, demo2)
        cid = dbm.scalar(conn, "SELECT id FROM conversations WHERE match_id=?", (mid,))
        for sender, body in ((demo2, "Hi Asha! I saw you're into trekking too — done any good trails near Lucknow?"),
                             (demo, "Hey Vikram! Not many hills here 😄 but I did Kedarkantha last winter. You?")):
            conn.execute("INSERT INTO messages (conversation_id, sender_id, kind, body, created_at) VALUES (?,?,?,?,?)",
                         (cid, sender, "text", body, dbm.iso()))
        conn.execute("UPDATE conversations SET last_message_at=? WHERE id=?", (dbm.iso(), cid))
    print(f"Seeded {args.count + 2} fictional members.")
    print("Demo logins: demo@heartsync.test and demo2@heartsync.test (they're matched with each other).")
    if os.environ.get("SEED_PASSWORD"):
        print("Password: the value of SEED_PASSWORD.")
    else:
        print(f"Generated password for all seed accounts: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
