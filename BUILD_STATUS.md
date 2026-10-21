# Build status

_Last updated: 21 October 2026_

**Summary:** the core journey works end to end against real persisted data and is covered by automated tests. That journey is sign-up, then verification, profile, discovery, matching, chat, phone sharing, safety and moderation, and billing entitlements. The app has **not been deployed**. External providers (Twilio, SMTP, Stripe) have adapters but have **not been exercised with real credentials**. Legal pages are drafts requiring counsel review.

## Test results (this build)

| Suite | Command | Result |
|---|---|---|
| API, security and integration (unittest; 175 tests across 8 files) | `python -m pytest -q` | **175 passed** |
| Browser end-to-end (Playwright/Chromium, 16 steps) | `bash e2e/run.sh` | **16/16 passed** |
| Lint (pyflakes + pycodestyle subset) | `ruff check heartsync scripts tests --select E,F,W --ignore E501` | clean |
| Production-mode boot with placeholder credentials | see DEPLOYMENT.md | starts. HSTS and `Secure` cookies present, `/dev/outbox` is 404, mock webhooks rejected, and incomplete config refuses to start |
| Docker image build | `docker compose build` | **not run** (no Docker daemon in the build environment) |

Every abuse scenario from the brief has at least one test that goes through the real HTTP API:

| Scenario | Test(s) |
|---|---|
| Unverified user attempting restricted actions | `test_unverified_user_cannot_use_dating_features` |
| Reading another user's messages | `test_outsider_cannot_read_or_send`, `test_conversation_id_dead_after_unmatch` |
| Blocked user sending messages | `test_blocked_user_cannot_send`, `BlockBypassTests` |
| Unauthenticated user calling admin APIs | `test_unauthenticated_admin_calls`, `test_regular_user_forbidden`, `AdminEscalationTests` |
| Exposing another user's phone number | `test_phone_never_exposed_without_consent`, `test_phone_not_in_logs_or_audit`, `test_admin_sees_masked_phone_only` |
| Forged or replayed payment callback | `test_forged_webhook_rejected`, `test_webhook_cannot_upgrade_a_different_user`, `test_replayed_webhook_with_old_timestamp_rejected` |
| Malicious or oversized attachment | `test_image_messages_and_malicious_uploads`, `test_photo_upload_strips_exif_and_rejects_bad_files`, `DecompressionBombTests` |
| Duplicate match requests | `test_duplicate_likes_are_idempotent`, `test_concurrent_mutual_likes_create_single_match`, `test_match_unique_constraint_in_database` |
| Invalid or expired OTPs | `test_invalid_otp`, `test_expired_otp`, `test_otp_attempt_limit_burns_code` |

## Implemented

**Accounts and security:** email/password sign-up (18+ enforced server-side), email verification, phone OTP verification, login/logout, password reset and change, session list and revoke, sign out other devices, account deactivation, deletion and data export. **Two-factor sign-in (TOTP)** with recovery codes, required for staff in production. Rate limits on all sensitive actions, CSRF protection, strict CSP and security headers.

**Profiles:** onboarding wizard, a full editor (basics, about, interests, lifestyle, values, dating intentions, partner qualities), per-field visibility, photo upload, ordering and removal (EXIF stripped), completeness meter, preview, hide profile, manual photo verification. **Writing help:** profile tips and "About me" drafts, labelled as suggestions and never saved automatically, with an optional Claude API adapter.

**Discovery:** swipe deck, grid search, daily picks, new members, recently active, opt-in nearby, compatibility sorting with plain-language reasons, cursor pagination, 30+ filters (strict or flexible), include/exclude lifestyle filters, saved presets, active-filter chips, result counts. Reciprocal preferences, blocking, visibility and suspension are applied server-side.

**Matching:** like, pass and Super Like with plan quotas; mutual-match detection (transaction plus unique constraint); match celebration; unmatch; undo (premium); incoming likes (identities premium-only); saved profiles; boosts.

**Chat:** real-time (SSE) messages, typing indicators, presence and last-seen (with privacy controls), unread counts, sent, delivered and read states (read receipts are premium), replies, reactions, edit and delete rules, image messages via private signed URLs, history pagination, search, archive, mute, block/report/unmatch from chat, reconnect handling, rule-based icebreakers.

**Phone sharing:** explicit, confirmed, per-match sharing; revoke; automatic revoke on unmatch or block; masked for admins; never in search, logs or notifications.

**Safety and moderation:** report profile or message (categories, details, evidence snapshot), block, auto-flag on repeated reports and immediately for underage reports, suspension and reinstatement, warnings, appeals, photo verification queue, spam indicators, audit log, role management, editable FAQ and safety content, admin metrics.

**Billing:** Free, Plus and Premium plans (configurable), simulated checkout in development, Stripe Checkout adapter, signed and idempotent webhooks, renewal, failure (past-due) and cancellation handling, billing history, server-side entitlements.

**Platform:** landing, pricing, help/FAQ, safety centre, legal drafts (terms, privacy, cookies, guidelines, refunds), notifications centre and preferences (quiet hours), light/dark themes, mobile tab bar, SEO metadata, robots and sitemap (public pages only), noindex on private pages, Dockerfile and compose, CI workflow, seed and admin scripts, backup script.

## Not done / known limitations

- **Not deployed.** No hosting credentials were available. Follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- **Providers unverified live.** Twilio, SMTP and Stripe adapters make real HTTP calls but were not run against live services. Run staging with test credentials first. Also check the Stripe API-version note in [docs/PROVIDERS.md](docs/PROVIDERS.md).
- **SQLite, not PostgreSQL.** The brief preferred PostgreSQL. This build environment had no network access to package registries, so the existing SQLite implementation was kept. The migration path is in [docs/DATABASE.md](docs/DATABASE.md).
- **Single process.** Realtime hub and rate limiter are in-memory. Move them to Redis before running more than one instance.
- **Not built (optional in the brief):** social login, voice notes, push notifications, QR code for 2FA setup (setup key and `otpauth://` link only), automated ID or biometric verification, an S3 storage adapter, CAPTCHA.
- **Legal:** all legal pages are drafts with `[placeholders]` and need review by qualified counsel, including for India's DPDP Act and consumer-protection rules. Their existence does not imply compliance.
- **Docker image** not built or tested here.

## Changes in this session

1. Continued the existing Python application rather than rewriting it (the brief asks to preserve useful work, and npm/pypi were unreachable).
2. Fixed the failing nearby-discovery test: the default radius is now 100 km, suited to city-level locations.
3. Added 17 adversarial tests (block bypass, signed media, path traversal, webhook replay, privilege escalation, decompression bombs). They found no access-control defects.
4. Added two-factor sign-in, the staff 2FA requirement, and 2FA status in the admin member view.
5. Added profile writing help, with a rules provider and an optional Claude API adapter.
6. **Fixed a `.env` parsing bug**: `KEY=   # comment` used to set KEY to the comment text, which could silently become `SECRET_KEY`.
7. Production now fails fast on missing provider credentials or non-HTTPS config. Added `PAYMENT_PROVIDER=none`.
8. Closed a 40–80 MP image-decoding gap. Account deletion now also removes 2FA data and preferences.
9. Fixed the swipe controls falling below the fold on laptops, and form field alignment.
10. Wrote the README, backup script, and the deployment, provider, security and database docs.

## How to continue

1. Deploy to staging with test-mode Twilio, SMTP and Stripe. Walk the go-live checklist in DEPLOYMENT.md.
2. Get legal review and fill the placeholders in `heartsync/templates/legal/`.
3. Commission a penetration test, using [docs/SECURITY.md](docs/SECURITY.md) as the scope document.
4. Before scaling past one server: Redis for the realtime hub and rate limits, PostgreSQL, and object storage.
