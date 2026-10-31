# Context Commerce

**Put your products in the videos you already have.** Upload two to four product images and your videos. Context Commerce finds the flat surfaces in every scene, decides which product fits where, and explains why. It then renders placements that pass a frame-by-frame quality check. There are no reshoots and no generative redrawing of your product.

- **Sign in** with Google, or with an emailed magic link and code. There are no passwords.
- **Decision engine:** 1–4 products × many videos → the best product for each video, its surface, its time window, a preview, a score and the reasons. See [docs/v11/decision-engine.md](docs/v11/decision-engine.md).
- **Check, approve, render:** every placement is measured on every frame and approved by a manager for that exact version. The output is validated before it can be downloaded through short-lived links.
- **Plans:** Free, Pro ($39, or $29 billed annually), Agency ($159, or $119 billed annually) and Enterprise, enforced by the API. See [docs/v11/pricing.md](docs/v11/pricing.md), which includes the competitor analysis.

**Status (V11):** runs locally and in Docker, with tests passing. Neon, Cloudflare R2, a real SMTP provider, Google sign-in and Vercel have never been connected, because no accounts were available. No real creator footage has been evaluated. See [docs/v11/known-limitations.md](docs/v11/known-limitations.md).

## Run it
**Docker** (PostgreSQL, S3 stand-in, email inbox, API, two workers, web app):
```bash
docker compose up --build
# app http://localhost:3000 · sign-in emails http://localhost:8025 · API docs http://localhost:8000/docs
```
**Without Docker** (Python 3.11+, Node 20+, FFmpeg). This uses SQLite, local disk and an email outbox in `data/outbox/`:
```bash
scripts/dev.sh setup
scripts/dev.sh run        # API + worker on :8000
scripts/dev.sh web        # second terminal: web app on http://localhost:3000
```
On Windows, use `.\scripts\dev.ps1 setup`, then `.\scripts\dev.ps1 all`, which starts the API, a worker and the web app. Open **http://localhost:3000**.

Sign-in locally: with no email provider configured, the sign-in page shows the magic link and code itself. For real email or Google, add your SMTP or Google OAuth settings to `.env` (see `.env.example`) and restart the API.

## Layout
| Path | What |
|---|---|
| `web/` | Next.js 16 app: landing, pricing, sign-in, dashboard ([docs/v11/web-app.md](docs/v11/web-app.md)) |
| `app/` | FastAPI API: auth, workspaces and roles, uploads, decisions, placements, quality policy, renderer, validation |
| `worker.py` | Render and decision worker (run one or more) |
| `ops/` | Migrations, backups, maintenance, plans, monitoring |
| `tests/`, `qa/` | Backend tests; browser QA, load test, outage drill |
| `benchmark/` | Synthetic fixtures and visual-quality benchmarks |
| `deploy/`, `docker-compose.yml`, `Dockerfile`, `web/Dockerfile` | Deployment |

## Test
```bash
PYTHONPATH=".:tests" python -m pytest -q        # Windows: PYTHONPATH=".;tests"
cd web && npm run typecheck && npm run build
python qa/browser_qa_web.py                     # needs a web build whose CC_API_ORIGIN matches --api-port
python qa/browser_qa_stack.py --app http://localhost:3000    # browser against the docker compose stack
```

## Deploy
Vercel hosts only the web app (root directory `web`). The API and workers run on a VM with Neon and R2, and they must be live first. See the step-by-step order in [docs/v11/deployment.md](docs/v11/deployment.md); the backend guides are in [docs/v10/](docs/v10/).

## Documentation
- [docs/v11/changes.md](docs/v11/changes.md): what V11 added, simplified and removed
- [docs/v11/test-evidence.md](docs/v11/test-evidence.md): what was tested and the results
- [docs/v11/known-limitations.md](docs/v11/known-limitations.md)
