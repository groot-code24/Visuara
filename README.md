# Visuara

**Put your products in the videos you already have.** Upload product images and your videos. Visuara finds the flat surfaces in every scene, decides which product fits where, and renders placements that pass a frame-by-frame check. There are no reshoots and no generative redrawing of your product.

- **Sign in** with Google or an emailed magic link and code. There are no passwords.
- **Decision engine:** 1–4 products × many videos gives the best product for each video, its surface, its time window, a preview, a score and the reasons. See [docs/decision-engine.md](docs/decision-engine.md).
- **Check, approve, render:** every placement is measured on every frame and approved for that exact version. Renders are validated before they can be downloaded through short-lived links.
- **Plans:** Free, Pro, Agency and Enterprise, enforced by the API. See [docs/pricing.md](docs/pricing.md).

## Deploy
The website, API and video jobs all run in one Vercel project, on the free plan by default. Follow [docs/vercel.md](docs/vercel.md).

## Run it locally
You need Python 3.11+, Node 20+ and FFmpeg. This uses SQLite, local disk and an email outbox in `data/outbox/`:
```bash
scripts/dev.sh setup
scripts/dev.sh run        # API and worker on :8000
scripts/dev.sh web        # second terminal: web app on http://localhost:3000
```
On Windows, use `.\scripts\dev.ps1 setup`, then `.\scripts\dev.ps1 all`. With no email provider configured, the sign-in page shows the magic link and code itself. `docker compose up -d` adds a local PostgreSQL and an email inbox.

## Layout
| Path | What |
|---|---|
| `web/` | Next.js app: landing, pricing, legal, sign-in, dashboard |
| `app/` | FastAPI API: auth, workspaces and roles, uploads, decisions, placements, quality, renderer, validation |
| `worker.py` | Job worker for running outside Vercel, and for local development |
| `ops/` | Migrations, backups, maintenance, plans |
| `tests/`, `qa/`, `benchmark/` | Backend tests, browser and deployment checks, synthetic test footage |
| `vercel.json`, `Dockerfile.vercel` | The deployment |

## Test
```bash
PYTHONPATH=".:tests" python -m pytest -q        # Windows: PYTHONPATH=".;tests"
cd web && npm run typecheck && npm run build
python qa/browser_qa_web.py                     # needs a web build; see the top of the file
python qa/vercel_rehearsal.py                   # the whole deployment, locally, under Vercel's limits
python qa/check_env.py                          # your .env.vercel: format and real logins, no values printed
```

## Security
Real settings belong only in `.env.vercel` and in Vercel. Git ignores that file, and the `.githooks/pre-commit` hook stops a commit containing a credential. Turn the hook on in a new clone with `git config core.hooksPath .githooks`.

## More
- [docs/known-limitations.md](docs/known-limitations.md)
- [docs/test-evidence.md](docs/test-evidence.md)
