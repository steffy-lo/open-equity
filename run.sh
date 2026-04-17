#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run.sh — Start the OpenClaw Paper Trading Server
# Usage: ./run.sh [--test] [--port 5000]
# ─────────────────────────────────────────────────────────────
set -euo pipefail

PORT=5000
RUN_TESTS=false

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --test)  RUN_TESTS=true; shift ;;
    --port)  PORT="$2";       shift 2 ;;
    *)       echo "Unknown arg: $1"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ──────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m';   NC='\033[0m'

log()  { echo -e "${GREEN}[run]${NC} $*"; }
warn() { echo -e "${YELLOW}[run]${NC} $*"; }
err()  { echo -e "${RED}[run]${NC} $*" >&2; }

# ── Virtualenv ────────────────────────────────────────────────
if [ -d "venv" ]; then
  log "Activating existing virtualenv..."
  source venv/bin/activate
else
  warn "No venv found — creating one..."
  python3 -m venv venv
  source venv/bin/activate
  log "Installing dependencies..."
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
  log "Dependencies installed."
fi

# ── .env ─────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  warn ".env not found — copying from .env.example"
  cp .env.example .env
  warn "Review .env before going live (STARTING_CASH, TRADING_FEE_PCT, etc.)"
fi

# ── Watchlist ─────────────────────────────────────────────────
if [ ! -f "watchlist.json" ]; then
  warn "watchlist.json missing — creating empty watchlist"
  echo '{"tickers": [], "updated_at": ""}' > watchlist.json
fi

log "─────────────────────────────────────────────────────"
log "  OpenClaw Paper Trading Server"
log "  Port   : $PORT"
log "  Docs   : http://localhost:$PORT/docs"
log "  Dashboard: http://localhost:$PORT/dashboard"
log "─────────────────────────────────────────────────────"

# ── Optional: run tests first ─────────────────────────────────
if $RUN_TESTS; then
  warn "Starting server in background for tests..."
  PORT=$PORT uvicorn main:app --host 0.0.0.0 --port "$PORT" --log-level warning &
  SERVER_PID=$!
  sleep 4

  log "Running smoke tests..."
  if python test_server.py --base "http://localhost:$PORT"; then
    log "All tests passed — killing test server."
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  else
    err "Tests failed. Check output above."
    kill "$SERVER_PID" 2>/dev/null || true
    exit 1
  fi
fi

# ── Start server (foreground) ─────────────────────────────────
log "Starting server on port $PORT..."
exec uvicorn main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --reload \
  --log-level info
