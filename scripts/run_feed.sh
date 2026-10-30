#!/usr/bin/env bash
# Shared FastAPI feed for ~10 concurrent users (no paid infra).
# Caps concurrency at the process boundary; keep-alives stay short.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
exec python3 -m uvicorn app.server:app \
  --host "$HOST" \
  --port "$PORT" \
  --limit-concurrency "${UVICORN_LIMIT_CONCURRENCY:-40}" \
  --timeout-keep-alive "${UVICORN_KEEPALIVE:-5}"
