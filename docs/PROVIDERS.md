# External providers

Each integration sits behind an adapter in `heartsync/providers/`. Development uses stand-ins that send nothing: email and SMS go to `/dev/outbox`, and checkout is simulated. Production refuses to start with a stand-in, or with a real provider whose credentials are missing.

| Integration | Status in this repo | Needs |
|---|---|---|
| SMS (phone OTP) | Twilio adapter written; **not exercised against the live API** | Twilio account, sender number |
| Email | SMTP adapter with STARTTLS; **not exercised against a live server** | Any SMTP provider |
| Payments | Stripe Checkout + signed webhooks; signature verification and event handling unit-tested; **not exercised against live Stripe** | Stripe account, two Prices, webhook secret |
| Writing suggestions | Local templates (default, works now); optional Claude API adapter | Optional: Anthropic API key |
| Photo verification | Manual review by moderators (works now) | None. Swap in a vendor later if wanted |
| Object storage | Local private directory with signed URLs (works now) | Optional: S3-compatible bucket (adapter not written) |

## SMS: Twilio

1. Create a Twilio account and buy or verify a sender number (or a Messaging Service).
2. Set:
   ```
   SMS_PROVIDER=twilio
   TWILIO_ACCOUNT_SID=AC…
   TWILIO_AUTH_TOKEN=…
   TWILIO_FROM_NUMBER=+1…
   ```
3. **India:** commercial SMS to Indian numbers requires DLT registration (entity, sender ID/header, and the OTP message template). Register through your provider before launch, or messages will be blocked. The OTP message text lives in `send_phone_otp` in `heartsync/services/accounts.py`.

The adapter makes one HTTPS POST to the Twilio Messages API. Codes are 6 digits, expire after 10 minutes, allow 5 attempts, and are stored only as keyed hashes. Sending is rate-limited per user and per number. The API never reveals whether a number is registered to someone else.

## Email: SMTP

Any SMTP service works (Amazon SES, Postmark, SendGrid, Mailgun, Zoho, Google Workspace relay…):

```
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=…
SMTP_PASSWORD=…
SMTP_FROM=HeartSync <no-reply@yourdomain>
```

Set up SPF, DKIM and DMARC for the sending domain. Emails cover verification, password reset, new match, subscription events and (opt-in) new messages. Message notifications never include message contents.

## Payments: Stripe

1. In Stripe, create a Product with two recurring monthly **Prices** (Plus, Premium) in your currency. Copy the `price_…` IDs.
2. Add a webhook endpoint at `https://<your-domain>/api/webhooks/stripe` subscribed to:
   - `checkout.session.completed`
   - `invoice.paid`
   - `invoice.payment_failed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
3. Set:
   ```
   PAYMENT_PROVIDER=stripe
   STRIPE_SECRET_KEY=sk_…             # use sk_test_… on staging
   STRIPE_WEBHOOK_SECRET=whsec_…
   STRIPE_PRICE_PLUS=price_…
   STRIPE_PRICE_PREMIUM=price_…
   ```
4. Keep the displayed prices in `PLANS_JSON` (or the defaults in `heartsync/config.py`) in sync with the Stripe Prices.

How entitlement works: premium access is granted **only** by a signature-verified webhook (HMAC with a 5-minute timestamp tolerance). Events are processed once (idempotent by event id). A checkout can only upgrade the user who started it. A failed renewal keeps access until the paid period ends, and cancellation keeps access until period end. The browser's success redirect grants nothing.

**Check before go-live:** in recent Stripe API versions, `current_period_end` on subscription objects moved to subscription items. The adapter takes renewal dates from `invoice.paid` line items. On `customer.subscription.updated` it reads `current_period_end` if present and otherwise keeps the stored date. Confirm against your account's API version on staging, using `stripe trigger` or test-mode checkouts.

**India:** for an Indian entity, Stripe India supports recurring card payments under RBI e-mandate rules. If you prefer Razorpay, implement `create_checkout`, `cancel_at_period_end`, `resume` and `parse_webhook` in `heartsync/providers/payments.py` for it. The rest of the app only sees normalised events.

To launch without paid plans: `PAYMENT_PROVIDER=none`. Upgrade buttons then show a "payments not configured" message.

## Writing suggestions: optional Claude API

Default `AI_PROVIDER=rules` builds suggestions locally from templates. No data leaves the server.

```
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=…
AI_MODEL=claude-haiku-5-5
```

Only the member's own non-sensitive profile fields are sent (never religion, family plans, relationship status, contact details, or anyone else's data), and only when the member clicks a button. Output is labelled as a suggestion, filtered for contact details, and never saved automatically. Any API error falls back to the local templates. Review the provider's data terms and update the privacy policy before enabling. The policy text already switches on this setting.

## Not implemented

- **Social login** (Google/Apple): not built. It would need an OIDC client in `services/accounts.py` plus provider credentials.
- **Automated photo/ID verification:** photo verification is a manual moderator review. No biometric or ID check is performed, and the badge says "Photo verified", not "ID verified".
- **Push notifications:** in-app and email only.
- **S3 storage adapter:** the interface (`save_image`, `read`, `delete`, `signed_url`) is ready for one.
