# Deploying HeartSync

HeartSync is one Python process serving HTML, a JSON API and Server-Sent Events, with a SQLite database and a private media directory on a persistent disk. Any host that runs a container (or Python 3.12+) with a persistent volume works, for example a VM, Fly.io, Railway or Render with a disk.

> Nothing has been deployed from this repository yet. The Docker image has not been built in the build environment (no Docker daemon there). The app *has* been started in production mode with placeholder credentials, and it served pages with production headers. Do a staging deploy first.

## 1. Prepare configuration

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"          # -> SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> MFA_ENCRYPTION_KEY
```

Required for `APP_ENV=production`. The app **refuses to start** and lists anything missing:

- `SECRET_KEY` (32+ chars), `BASE_URL=https://…`, `COOKIE_SECURE=true`
- `SMS_PROVIDER=twilio` + `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
- `EMAIL_PROVIDER=smtp` + `SMTP_HOST` (+ `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`)
- `PAYMENT_PROVIDER=stripe` + all four `STRIPE_*` values, **or** `PAYMENT_PROVIDER=none` to launch free-only
- `TRUST_PROXY=1` only if a reverse proxy you control sets `X-Forwarded-For` (rate limits use the client IP)

Recommended: set `MFA_ENCRYPTION_KEY` so rotating `SECRET_KEY` doesn't invalidate 2FA enrolments. Keep `.env` out of version control (it's in `.gitignore`); prefer your host's secret store.

Provider account setup: [PROVIDERS.md](PROVIDERS.md).

## 2a. Docker (recommended)

```bash
docker compose up --build -d
docker compose logs -f web          # migrations run automatically at startup
curl -fsS http://localhost:8000/healthz
```

The image runs as an unprivileged user and stores everything under the `/data` volume (`heartsync.db`, `media/`). It includes a health check.

## 2b. Plain VM (systemd)

```bash
sudo useradd --system --home /srv/heartsync heartsync
sudo -u heartsync git clone <repo> /srv/heartsync/app && cd /srv/heartsync/app
sudo -u heartsync python3 -m venv .venv && sudo -u heartsync .venv/bin/pip install -r requirements.txt
```

`/etc/systemd/system/heartsync.service`:

```ini
[Unit]
Description=HeartSync
After=network.target

[Service]
User=heartsync
WorkingDirectory=/srv/heartsync/app
EnvironmentFile=/srv/heartsync/app/.env
ExecStart=/srv/heartsync/app/.venv/bin/uvicorn heartsync.main:app --host 127.0.0.1 --port 8000 --proxy-headers --workers 1
Restart=always
NoNewPrivileges=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

## 3. Reverse proxy and HTTPS

Terminate TLS at a proxy (Caddy, nginx, or your platform's load balancer). Requirements:

- Disable response buffering for `/api/stream`, which is Server-Sent Events. It must stream.
- Allow request bodies of at least `MAX_UPLOAD_BYTES` plus 64 KB (default 8 MB).
- Long read timeouts on `/api/stream` (the server sends a keep-alive comment periodically).

Caddy (automatic HTTPS):

```
heartsync.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
    request_body { max_size 9MB }
}
```

nginx:

```nginx
location /api/stream { proxy_pass http://127.0.0.1:8000; proxy_buffering off; proxy_read_timeout 1h; }
location / { proxy_pass http://127.0.0.1:8000; client_max_body_size 9m;
             proxy_set_header Host $host; proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Forwarded-Proto https; }
```

## 4. First admin

```bash
ADMIN_EMAIL=you@yourdomain ADMIN_PASSWORD='long-unique-passphrase' python scripts/create_admin.py
# Docker: docker compose exec -e ADMIN_EMAIL=… -e ADMIN_PASSWORD=… web python scripts/create_admin.py
```

Sign in, go to **Settings → Security** and turn on two-factor sign-in. In production, admin tools stay locked until you do (`REQUIRE_STAFF_MFA`). There is no default admin account.

Then edit the FAQ and safety content under **Admin → Help content**, and fill in the placeholders in the legal pages (`heartsync/templates/legal/`, marked `[…]`) after legal review.

## 5. Backups and recovery

```bash
python scripts/backup.py /var/backups/heartsync --keep 14      # cron: daily
```

This takes a consistent online copy of the database (SQLite backup API plus integrity check) and a tarball of private media. Backups contain personal data: encrypt them (for example `restic` or `age` before upload) and restrict access. Deleted accounts' data persists in backups until they rotate out. State that period in the privacy policy.

**Restore:** stop the app, copy `heartsync.db` to `DATABASE_PATH`, extract `media.tar.gz` into `MEDIA_ROOT`, start the app. Test a restore before launch, and periodically after.

For point-in-time recovery, consider [Litestream](https://litestream.io/) streaming the SQLite WAL to object storage.

## 6. Scaling notes

The current design targets one server, which handles a launch-sized community comfortably. Before running more than one app process:

| Component | Today | Multi-instance replacement |
|---|---|---|
| Realtime hub (`heartsync/realtime.py`) | in-process queues | Redis pub/sub; `publish()` is the only call site to change |
| Rate limiter (`heartsync/security.py`) | in-process sliding window | Redis-backed limiter with the same `allow()` interface |
| Database | SQLite (WAL) | PostgreSQL; see [DATABASE.md](DATABASE.md) |
| Media (`heartsync/providers/storage.py`) | local private directory + signed URLs | S3/GCS/R2 private bucket + provider pre-signed URLs |

## 7. Monitoring

- Health check: `GET /healthz` (touches the database).
- Logs go to stdout. Unhandled errors are logged with stack traces server-side only, and clients get a generic message. Phone numbers and message contents are never logged.
- Error-tracking hook: `server_error` in `heartsync/main.py` is the single place to add Sentry or similar.
- Admin overview shows activity metrics, open reports, auto-flagged accounts and spam indicators.

## 8. Go-live checklist

- [ ] Staging deploy works end to end with real Twilio, SMTP and Stripe **test** credentials
- [ ] Stripe webhook endpoint registered, and a test event delivered (Stripe dashboard shows 200)
- [ ] `COOKIE_SECURE=true`, HTTPS only, HSTS header present (`curl -I`)
- [ ] `/dev/outbox` returns 404 and no dev banner appears
- [ ] Admin created, with 2FA on; moderators trained on the reports queue
- [ ] Legal pages reviewed by counsel and placeholders filled (grievance officer, retention periods, company details)
- [ ] Backups scheduled, and a restore tested
- [ ] DLT registration for SMS sender ID and templates (India), if using Indian numbers
- [ ] Load test discovery and chat at expected peak
