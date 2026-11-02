#!/usr/bin/env bash
# Local development without Docker (Linux/macOS/Git Bash): SQLite + local disk + email outbox.
#   scripts/dev.sh setup    create .venv, install Python and web dependencies (Python 3.11+, Node 20+ and FFmpeg required)
#   scripts/dev.sh run      start the API on http://127.0.0.1:8000 and one worker; Ctrl+C stops both
#   scripts/dev.sh web      start the web app on http://localhost:3000 (in a second terminal); open this one
#   scripts/dev.sh test     run the backend test suite
# For PostgreSQL and a real SMTP inbox locally: docker compose up -d
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
VENV=.venv
BIN="$VENV/bin"; [ -d "$VENV/Scripts" ] && BIN="$VENV/Scripts"
export CC_MAIL_BACKEND="${CC_MAIL_BACKEND:-outbox}" CC_INLINE_WORKER=0

case "${1:-run}" in
  setup)
    command -v ffmpeg > /dev/null || { echo "FFmpeg is required (apt install ffmpeg / brew install ffmpeg / winget install ffmpeg)" >&2; exit 1; }
    "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
    "$PY" -m venv "$VENV"
    "$BIN/python" -m pip install -q --upgrade pip
    "$BIN/python" -m pip install -q -r requirements-dev.txt
    [ -f .env ] || cp .env.example .env
    "$BIN/python" -m ops.migrate
    (cd web && npm ci --no-audit --no-fund)
    echo "setup complete: scripts/dev.sh run   (and scripts/dev.sh web in a second terminal)" ;;
  run)
    "$BIN/python" worker.py & WORKER=$!
    trap 'kill $WORKER 2>/dev/null || true' EXIT
    "$BIN/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-keep-alive 75 --reload ;;
  web)
    cd web && CC_API_ORIGIN="${CC_API_ORIGIN:-http://127.0.0.1:8000}" npm run dev ;;
  test)
    PYTHONPATH=".:tests" "$BIN/python" -m pytest -q -p no:cacheprovider ;;
  *) echo "usage: scripts/dev.sh setup|run|web|test" >&2; exit 2 ;;
esac
