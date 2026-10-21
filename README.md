# HeartSync

A dating platform for adults (18+), built around privacy and safety: verified accounts, detailed profiles with per-field visibility, discovery with extensive filters, mutual matching, real-time chat, consent-based phone sharing, blocking/reporting with a moderation dashboard, and optional premium plans.

Brand name, tagline, colours and plan quotas are configurable through environment variables. Launch market defaults are India (INR pricing, Indian cities); see [Configuration](#configuration).

> **Status:** feature-complete for the core journey and tested. It is **not deployed**. Real SMS, email and payment credentials are still needed. Legal pages are drafts that need review by qualified counsel. See [BUILD_STATUS.md](BUILD_STATUS.md).

## Stack

| Layer | Choice |
|---|---|
| Server | Python 3.13, [Starlette](https://www.starlette.io/) (ASGI), Uvicorn |
| Templates / UI | Jinja2 server-rendered pages + vanilla JS modules, hand-written CSS design system (light/dark, mobile-first) |
| Validation | Pydantic v2 on every API input |
| Database | SQLite (WAL) with SQL migrations; schema written to port to PostgreSQL ([docs/DATABASE.md](docs/DATABASE.md)) |
| Realtime | Server-Sent Events from an in-process hub (chat, typing, presence, notifications) |
| Media | Private directory, served only through short-lived HMAC-signed URLs; images re-encoded, EXIF/GPS stripped |
| Providers | Adapters for Twilio (SMS), SMTP (email), Stripe (payments), Claude API (optional writing help), each with a dev-only stand-in that production refuses to run |

There is no build step. Python, the packages in `requirements.txt`, and (for browser tests) Playwright are all you need.

## Quick start (local development)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                       # defaults are fine for local dev

# Fictional demo members (generated abstract photos, no real people)
SEED_PASSWORD='Demo-pass-2026!' python scripts/seed.py --count 30

# Optional: an admin account (credentials come from env, never hard-coded)
ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD='a-long-unique-passphrase' python scripts/create_admin.py

uvicorn heartsync.main:app --reload --port 8000
```

Open http://localhost:8000 and sign in as `demo@heartsync.test` or `demo2@heartsync.test` (matched with each other) using your `SEED_PASSWORD`.

In development, email and SMS are **not sent**. They appear at **/dev/outbox**, where you'll find verification links and OTP codes. Payments use a simulated checkout. A banner on every page says so. None of these stand-ins can run when `APP_ENV=production`.

## Tests

```bash
python -m unittest discover -s tests -t .      # or: python -m pytest -q
bash e2e/run.sh                                # browser tests (needs: npm i playwright && npx playwright install chromium)
ruff check heartsync scripts tests --select E,F,W --ignore E501
```

All tests drive the real HTTP API against a fresh database per test. Nothing is mocked except outbound provider calls. Latest results are in [BUILD_STATUS.md](BUILD_STATUS.md).

## Configuration

Everything is set through environment variables. `.env.example` lists every one, with placeholders only. The important ones:

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development`, `test` or `production`. Production refuses dev stand-ins and fails at startup if required settings are missing. |
| `SECRET_KEY` | 32+ random characters. Signs sessions, tokens and media URLs. |
| `BASE_URL`, `COOKIE_SECURE` | Public `https://` URL; `true` in production. |
| `SMS_PROVIDER` + `TWILIO_*` | Phone verification. |
| `EMAIL_PROVIDER` + `SMTP_*` | Verification, password reset, notifications. |
| `PAYMENT_PROVIDER` + `STRIPE_*` | `stripe`, or `none` to launch with the free plan only. |
| `BRAND_*`, `SUPPORT_EMAIL` | Re-branding without code changes. |
| `PLANS_JSON` | Override plan prices and quotas (structure in `heartsync/config.py`). |
| `REQUIRE_STAFF_MFA` | Staff must turn on two-factor sign-in before using admin tools (default on in production). |
| `AI_PROVIDER`, `ANTHROPIC_API_KEY` | Optional writing suggestions; defaults to local templates. |

Provider setup steps are in [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Deploying

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for Docker or plain-VM deployment, reverse proxy, backups and the go-live checklist. Short version:

```bash
cp .env.example .env    # fill in production values
docker compose up --build -d
docker compose exec -e ADMIN_EMAIL=you@example.com -e ADMIN_PASSWORD='long-unique-passphrase' \
  web python scripts/create_admin.py
```

Run a **single** app process. Realtime chat uses an in-process event hub (see the scaling notes in DEPLOYMENT.md).

## Project layout

```
heartsync/
  main.py            app factory, CSRF + security-header middleware, error handling
  config.py          settings, plans/entitlements, production checks
  web.py             auth guards, validation, error helpers
  security.py        scrypt passwords, token hashing, signing, rate limits
  db.py              SQLite connection, transactions, migrations
  realtime.py        SSE event hub
  catalog.py         profile option lists and city coordinates
  migrations/        numbered .sql migrations (applied at startup)
  providers/         messaging (SMS/email), payments, storage, ai
  services/          accounts, mfa, profiles, discovery, matching, chat, phone,
                     safety, notifications, billing, admin, assist, audit
  routes/            api.py (JSON API), pages.py (HTML pages)
  templates/, static/
scripts/             seed.py, create_admin.py, backup.py
tests/               API, security and integration tests (unittest)
e2e/                 Playwright browser tests
docs/                deployment, providers, database, security
```

## Documentation

- [BUILD_STATUS.md](BUILD_STATUS.md): what's done, what's tested, what's left
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): deploying, backups, scaling, go-live checklist
- [docs/PROVIDERS.md](docs/PROVIDERS.md): Twilio, SMTP, Stripe, AI setup
- [docs/SECURITY.md](docs/SECURITY.md): security and privacy design, checklist
- [docs/DATABASE.md](docs/DATABASE.md): schema overview, retention, PostgreSQL migration
