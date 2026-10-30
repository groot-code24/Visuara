#!/usr/bin/env bash
# Local development without Docker (Linux/macOS/Git Bash): SQLite + local disk + email outbox, API and a separate worker.
#   scripts/dev.sh setup    create .venv and install dependencies (Python 3.11+ and FFmpeg must be installed)
#   scripts/dev.sh run      start the API on http://127.0.0.1:8000 and one worker; Ctrl+C stops both
#   scripts/dev.sh test     run the test suite
# For the production-shaped stack (PostgreSQL, S3 stand-in, real SMTP inbox, two workers) use: docker compose up --build
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
    echo "setup complete: scripts/dev.sh run" ;;
  run)
    "$BIN/python" worker.py & WORKER=$!
    trap 'kill $WORKER 2>/dev/null || true' EXIT
    "$BIN/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload ;;
  test)
    PYTHONPATH=".:tests" "$BIN/python" -m pytest -q -p no:cacheprovider ;;
  *) echo "usage: scripts/dev.sh setup|run|test" >&2; exit 2 ;;
esac
