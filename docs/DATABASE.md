# Database

SQLite in WAL mode, accessed with bound-parameter SQL (no ORM). Migrations are numbered files in `heartsync/migrations/`, applied in order at startup and recorded in `schema_migrations`. To change the schema, add a new file (for example `004_….sql`). Never edit an applied one.

Timestamps are ISO-8601 UTC strings (`YYYY-MM-DDTHH:MM:SS.ffffffZ`), which sort correctly as text. IDs are UUID4 hex strings, except messages and notifications, which use integer autoincrement for ordering.

## Tables

| Area | Tables | Notes |
|---|---|---|
| Accounts | `users`, `sessions`, `auth_tokens`, `otp_codes` | DOB never exposed; tokens/codes stored as keyed hashes |
| Contact data | `user_contacts` | Phone numbers, **separate from profiles**; only read by phone-verification and consented sharing |
| Two-factor | `user_mfa`, `mfa_recovery_codes`, `mfa_challenges` | Encrypted TOTP secret; hashed one-time codes |
| Profiles | `profiles`, `profile_photos`, `photo_verifications`, `interests`, `profile_interests`, `dating_preferences` | City-level coordinates only; per-field visibility JSON |
| Discovery | `swipes`, `profile_views`, `saved_profiles`, `filter_presets`, `boosts` | `swipes` PK `(from_user, to_user)` prevents duplicate likes |
| Matching & chat | `matches`, `conversations`, `conversation_participants`, `messages`, `message_attachments`, `message_reactions`, `phone_shares` | `matches` UNIQUE `(user_a, user_b)` with `CHECK (user_a < user_b)`: one match per pair, enforced by the database |
| Safety | `blocks`, `reports`, `moderation_actions`, `appeals` | Evidence snapshots are staff-only |
| Notifications | `notifications`, `notification_preferences` | `UNIQUE (user_id, dedupe_key)` suppresses repeats |
| Billing | `subscriptions`, `payments`, `checkout_sessions`, `webhook_events` | `webhook_events` PK = provider event id (idempotency) |
| Admin | `audit_logs`, `content_pages` | Audit log never stores phone numbers or message bodies |
| Dev only | `dev_outbox` | Captured email/SMS in development; never written in production |

Mutual-match creation runs inside `BEGIN IMMEDIATE`, so two simultaneous likes produce exactly one match and one conversation (`test_concurrent_mutual_likes_create_single_match`).

## Data retention

When a member deletes their account (`accounts.delete_account`), these are **deleted immediately**: profile, photos and verification selfies (database rows and files), interests, likes and passes, saved profiles, filter presets, profile views, notifications, discovery preferences, OTP codes, auth tokens, 2FA secret and recovery codes, and phone shares. The phone number is nulled. Their sent message bodies and attachments are removed, their matches are closed, and their sessions are revoked.

**Retained**, because safety, other members and the law need them:

- A tombstone `users` row with no personal data (email replaced with `deleted-<id>@invalid`, password disabled, DOB replaced with a placeholder). It keeps foreign keys intact for the other person's conversation history ("message deleted") and for moderation records.
- Reports about or by the account, moderation actions, appeals and audit-log entries.
- `subscriptions` and `payments` records needed for tax and accounting.
- Blocks, so a blocked person stays blocked against that account ID.

Backups keep deleted data until they rotate out (14 days with `scripts/backup.py --keep 14`). Counsel should set and publish exact retention periods. The privacy policy has a placeholder for them.

**Deactivation** is different: it hides the profile and keeps all data, and signing in again reactivates the account.

## Porting to PostgreSQL

The schema was written to port with mechanical changes. When you outgrow a single server:

1. **Types:** `TEXT` timestamps → `timestamptz` (or keep as text). `INTEGER` booleans → `boolean`. JSON-in-`TEXT` columns (`interested_in`, `languages`, `field_visibility`, …) → `jsonb`. `INTEGER PRIMARY KEY AUTOINCREMENT` → `bigint generated always as identity`.
2. **Upserts:** `INSERT … ON CONFLICT … DO UPDATE` and `INSERT OR IGNORE` (→ `ON CONFLICT DO NOTHING`) are used in a handful of places. Grep for `OR IGNORE`.
3. **Transactions:** `db.tx()` issues `BEGIN IMMEDIATE`. In Postgres use a normal transaction and `SELECT … FOR UPDATE` on the swipe row (or rely on the `matches` unique constraint with `ON CONFLICT DO NOTHING`) for mutual-match detection.
4. **Placeholders:** queries use `?` and `:name`. With `psycopg` switch to `%s` / `%(name)s`, or wrap the connection to translate.
5. **Search:** discovery uses bounding-box SQL plus haversine in Python over at most 500 candidates (`CANDIDATE_CAP`). In Postgres, PostGIS or `earthdistance` can move the distance filter into SQL.
6. **Migrate data:** `sqlite3 heartsync.db .dump`, adjust types, load, then verify row counts per table.

Row-level security isn't used. Authorization is enforced in the service layer on every query. If you expose the database to other services, add database roles that can't read `user_contacts`, `user_mfa` or `sessions`.

## Indexes

Defined in the migrations for: discovery (`visibility, gender`; `country, region, city`; geo bounding box; height; DOB; status; last active; created), swipes by target and by time, messages by conversation, notifications by user, reports by status and target, sessions by user, payments by user, audit log by time and target.
