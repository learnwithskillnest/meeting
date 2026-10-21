#!/usr/bin/env bash
# Browser end-to-end tests against a real server with a throwaway database.
set -euo pipefail
cd "$(dirname "$0")/.."

WORK="$(mktemp -d)"
PORT="${E2E_PORT:-8765}"
export APP_ENV=development
export DATABASE_PATH="$WORK/e2e.db"
export MEDIA_ROOT="$WORK/media"
export SECRET_KEY="e2e-secret-key-not-for-production-0123456789"
export SEED_PASSWORD="Demo-pass-2026!"
export BASE_URL="http://127.0.0.1:$PORT"

python3 scripts/seed.py --count 12 >/dev/null
python3 -m uvicorn heartsync.main:app --port "$PORT" --log-level warning &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$WORK"' EXIT

for _ in $(seq 1 50); do
  curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 0.2
done

E2E_BASE="http://127.0.0.1:$PORT" E2E_PHOTO="$WORK/photo.jpg" node e2e/e2e.js
