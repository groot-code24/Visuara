# Local development

No live Google, SMTP, Neon or R2 account is needed. Two ways to run it:

## A. Production-shaped stack (Docker): recommended
Requires Docker Desktop or Docker Engine with Compose v2.
```bash
docker compose up --build
```
| URL | What |
|---|---|
| http://localhost:8000 | Landing page; app at `/app` (served by the API container) |
| http://localhost:8025 | Mailpit inbox: every sign-in code, magic link and invitation is delivered here over real SMTP |
| 127.0.0.1:5433 | PostgreSQL 16 (`cc` / `cc-local-only`, database `context_commerce`) |
| http://localhost:8333 | S3 gateway (SeaweedFS) with access keys; anonymous access refused |

Services: `postgres`, `s3` (+ `s3-init`, which creates the private bucket), `mailpit`, `api`, and `worker` × 2 (independent processes sharing the database and a data volume). The credentials in `docker-compose.yml` are for this local stack only.

Useful commands:
```bash
docker compose ps                       # health of every service
docker compose logs -f api worker       # structured logs
docker compose exec api python -m ops.migrate
docker compose down                     # stop (data volumes are kept); add -v to wipe them
```

**Split-origin test (the Vercel layout):** the frontend on its own origin, as in production.
```bash
docker compose -f docker-compose.yml -f deploy/local/compose.split-origin.yml up -d --build
CC_API_ORIGIN=http://api.cc.localhost:8000 CC_STORAGE_ORIGIN=http://localhost:8333 node frontend/build.mjs
python qa/vercel_emulator.py --port 3000          # then open http://app.cc.localhost:3000 in Chrome
```

## B. Without Docker (SQLite, local disk, email outbox)
Requirements: Python 3.11+ (3.12 recommended) and FFmpeg/FFprobe on `PATH`.

Linux / macOS / Git Bash:
```bash
scripts/dev.sh setup     # venv, dependencies, .env from .env.example, schema
scripts/dev.sh run       # API on http://127.0.0.1:8000 + one worker process
scripts/dev.sh test
```
Windows PowerShell:
```powershell
.\scripts\dev.ps1 setup
.\scripts\dev.ps1 run
.\scripts\dev.ps1 test
```
Manual equivalent:
```bash
python -m venv .venv && . .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
python -m ops.migrate
uvicorn app.main:app --host 127.0.0.1 --port 8000        # terminal 1 (CC_INLINE_WORKER=1 also runs a worker inside)
python worker.py                                          # terminal 2 (with CC_INLINE_WORKER=0 in .env)
```
Email sign-in writes messages to `data/outbox/` (`latest.json` holds the last one). That is development only, and the app refuses it when `CC_ENV=production`.

## Tests
```bash
PYTHONPATH=".:tests" python -m pytest -q                       # SQLite (Windows: PYTHONPATH=".;tests")
CC_TEST_DATABASE_URL=postgresql://cc:cc-local-only@127.0.0.1:5433/context_commerce PYTHONPATH=".:tests" python -m pytest -q
python qa/browser_qa.py; python qa/browser_qa_v8.py; python qa/browser_qa_v9.py   # same-origin browser QA (own server)
python qa/browser_qa_v10.py                                    # split-origin QA against the compose stack
python qa/load_10_orgs.py --api http://localhost:8000          # 10-organization concurrency test against the compose stack
node frontend/build.mjs && node frontend/check.mjs
python qa/secret_scan.py
```
