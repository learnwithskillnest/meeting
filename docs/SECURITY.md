# Security and privacy design

This summarises the controls in the code and the tests that exercise them. It is an engineering description, not a compliance certification. Commission an independent penetration test before launch.

## Accounts and sessions

| Control | Where | Tested in |
|---|---|---|
| Passwords hashed with scrypt (N=2^14, r=8, p=1), common/weak passwords rejected | `security.py` | `test_auth`, `test_platform` |
| Same error and equal timing for unknown email vs wrong password | `accounts.authenticate` | `test_unknown_email_same_error_as_wrong_password` |
| Session token: 256-bit random, stored as a keyed HMAC, `HttpOnly`, `SameSite=Lax`, `Secure` in production, server-side revocation | `accounts.py` | `test_session_cookie_flags`, `test_login_logout_and_session_revocation` |
| "Sign out other devices", per-session revoke (IDOR-safe) | `api.py` | `test_session_revoke_idor` |
| Email verification and password-reset tokens: single use, expiring, hashed | `accounts.py` | `test_password_reset_flow_and_single_use`, `test_expired_reset_token` |
| Phone OTP: 6 digits, 10 min expiry, 5 attempts then burned, hashed, rate-limited per user, IP and number; never reveals whether a number is registered | `accounts.py` | `test_auth` (OTP tests) |
| Optional two-factor (TOTP): secret encrypted at rest (Fernet), replay-protected, 10 hashed single-use recovery codes; login challenge expires in 5 min and burns after 5 tries; enabling signs out other devices; password reset cannot bypass it | `services/mfa.py` | `test_mfa` |
| Staff must have 2FA to use admin tools (`REQUIRE_STAFF_MFA`, on by default in production) | `web.require_staff`, `pages.page` | `StaffMfaPolicyTests` |
| Rate limits on sign-up, login, reset, OTP, 2FA, swipes, messages, uploads, reports, phone sharing | `security.LIMITS` | `test_login_rate_limited`, `test_send_rate_limited`, `test_otp_send_rate_limited` |

## Age and verification gates

- Sign-up requires a date of birth showing 18+ (checked server-side, including the exact 18th birthday), plus explicit adult and terms confirmation.
- Every dating API (`require_dating`) requires an active, email-verified, phone-verified, onboarded account. Pages redirect to the next missing step.
- A report in the "may be under 18" category removes the account from discovery immediately, pending review.
- Badges say exactly what was checked: "Phone verified" and "Photo verified" (manual moderator review of a selfie). No identity-document check exists, and nothing claims one.

## Authorization and privacy

- **Conversations and messages:** every operation looks up the conversation through the caller's own active match. Anything else returns the same 404 as a missing ID, so IDs can't be probed (`test_outsider_cannot_read_or_send`, `test_conversation_id_dead_after_unmatch`).
- **Blocking** applies in every query path: discovery (both directions), profile view by ID, likes, incoming likes, matching, messaging, image upload, typing, history, and phone sharing. Unblocking doesn't revive a match (`BlockBypassTests`).
- **Phone numbers** live in `user_contacts`, separate from profiles. They are never in profile, discovery, search, notification, log or audit output. A member shares their number with one match by explicit confirmed action, can revoke it, and unmatch or block revokes it automatically. Admins see a masked number only (`test_phone_*`, `test_admin_sees_masked_phone_only`).
- **Location:** only city centroids are stored (no GPS). Distances are shown as rounded labels and coordinates are never returned (`test_results_never_leak_private_fields`).
- **Per-field visibility** (everyone / matches / hidden) on sensitive profile fields. Defaults are conservative.
- **Sensitive attributes in search:** religion and similar filters are *include-only* and match only people who chose to show that field publicly. Exclusion filters exist only for lifestyle choices (smoking, drinking, diet, pets). Unknown filter keys are ignored and can't widen results (`test_sensitive_filter_only_matches_public_values`, `DiscoveryBypassTests`).
- **Premium entitlements** are computed server-side from the subscriptions table, which only verified webhooks write. Free users get a like *count* with no identities (`BillingAbuseTests`, `test_client_cannot_self_grant`, `test_forged_webhook_rejected`).
- **Admin APIs:** role checks run on the server for every endpoint. Moderators can't reach admin-only APIs. Every moderation action is written to the audit log. There is no default admin account (`test_unauthenticated_admin_calls`, `AdminEscalationTests`, `test_no_hardcoded_admin_exists`).

## Web protections

- CSRF: double-submit token (cookie + `X-CSRF-Token` header, constant-time compare) plus an Origin check on every non-GET API call. Webhooks are exempt and use signatures instead.
- Security headers on every response: strict CSP (`script-src 'self'`, no inline script), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP, and HSTS when `COOKIE_SECURE=true`.
- Output escaping: Jinja2 autoescape, and an `HS.esc` helper on all client-side rendering of user content.
- SQL: bound parameters only. Filter field names come from allow-lists (`test_injection_attempts_are_inert`).
- Open redirects blocked (`safe_next`). Private pages send `X-Robots-Tag: noindex` and `Cache-Control: no-store`. `robots.txt` and the sitemap list public pages only.
- Errors: clients get generic messages. Stack traces are logged server-side only.

## Uploads and media

- Images are decoded and re-encoded with Pillow, which strips EXIF (including GPS). Size, format and pixel dimensions (max 40 MP, checked from the header before decoding) are verified on the server, and non-images or oversized files are rejected (`test_photo_upload_strips_exif_and_rejects_bad_files`, `test_image_messages_and_malicious_uploads`).
- Media lives outside the web root and is served only via HMAC-signed, expiring URLs bound to the exact file key. Path traversal is rejected (`MediaAccessTests`).

## Payments

- Webhooks: HMAC-SHA256 signature over timestamp and body, 5-minute tolerance (replay protection), idempotent by event ID. A checkout can only upgrade its own user, and the success redirect grants nothing (`test_safety_admin_billing`, `test_replayed_webhook_with_old_timestamp_rejected`).

## Safety operations

- Report categories with optional details. An evidence snapshot is captured at report time and visible to staff only.
- Automatic risk flag when 3+ distinct members report someone within 7 days: they are hidden from discovery until reviewed.
- Suspension with reason, an appeals workflow, and an audit trail. Bios and names that look like contact details are rejected.

## Known gaps and production hardening

- Rate limiting and the realtime hub are in-process. Behind multiple instances, move both to Redis (see DEPLOYMENT.md).
- No malware scanning of uploads. Images are re-encoded, which neutralises most payloads, and no other file types are accepted.
- No CAPTCHA on sign-up. Add one (for example Cloudflare Turnstile) if bot sign-ups appear.
- No automated content moderation of message text beyond rate limits and the spam indicators in the admin dashboard.
- SQLite file permissions and backups need host-level protection: encrypted disk, restricted access, encrypted backups.

## Pre-launch security checklist

- [ ] Production env: `APP_ENV=production`, strong `SECRET_KEY`, `MFA_ENCRYPTION_KEY`, `COOKIE_SECURE=true`, HTTPS only
- [ ] `TRUST_PROXY=1` only behind your own proxy
- [ ] All staff accounts have 2FA. Remove staff role from anyone who doesn't need it
- [ ] Secrets in the host's secret store, not committed; `.env` permissions `600`
- [ ] Disk encryption on the data volume; encrypted, access-controlled backups; restore tested
- [ ] Independent penetration test, and a review of this document against the deployed system
- [ ] Incident response contact and process; grievance officer named in the legal pages (India)
- [ ] Dependency updates scheduled (`pip list --outdated`), with CI running on every change
