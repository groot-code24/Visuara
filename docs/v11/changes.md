# What changed in V11

V11 refocuses the product on one self-serve job: **put your products into the videos you own.** You can now:
- sign in with Google or a magic link;
- let the decision engine choose which product goes where;
- check, approve and render.

## Added
- **Next.js web app** (`web/`): landing, pricing, Anthropic-style sign-in (Google, or email with a magic link and code), onboarding, and the dashboard (overview, new decision, decision results, library, placements, renders, settings). The dashboard is protected by the proxy and a server-side session check. See [web-app.md](web-app.md).
- **Decision engine** (`app/decisions.py`): 1–4 products × many videos. It finds surfaces, tracks them, scores each product with the quality policy and six fit factors, assigns products across videos exactly, and explains each choice. It runs on the workers and is turned into placements on request. See [decision-engine.md](decision-engine.md).
- **Plans** (Free, Pro, Agency, Enterprise; prices in [pricing.md](pricing.md)) enforced by the API: decisions and renders per month, videos per decision, video length, storage, seats. `ops/plans.py` changes a workspace's plan. See [pricing.md](pricing.md).
- **Product categories:** beverage, food, beauty, fashion, tech, home, fitness, other. The engine uses them for scene context.
- **Infrastructure:**
  - a web Docker image and a `web` service in docker compose;
  - a CI job for the web app (`npm audit`, type check, build) and its image;
  - uvicorn runs with `--timeout-keep-alive 75`, so the Next.js proxy never reuses a connection the API has just closed. The load test found that race.
- **Tests:**
  - **Decision engine:** `tests/test_decisions.py`, including the check that recommendations pass their own quality check.
  - **Passwordless test helpers:** `tests/helpers.py`.
  - **Browser QA:** `qa/browser_qa_web.py`, 11 steps in headless Chrome against the real API and `next start`.
- **Marketing site redesign (31 October 2026):**
  - a dark hero with an interactive decision demo (`web/components/marketing/scene-lab.tsx`): pick a product, watch the scan, the scores using the engine's real weights, and the placement inside its time window;
  - a before/after slider;
  - a feature grid with small live visuals;
  - an agency section with a savings calculator;
  - pricing with per-render prices;
  - a mobile menu.
  Everything shown is labelled as an illustration, and there are no invented logos, testimonials or numbers.
- **Pricing refresh (31 October 2026):** Pro is $39 ($29 annual), and Team is renamed **Agency** at $159 ($119 annual). The annual discount is now 25%. The reasons are in [pricing.md](pricing.md).

- **Pre-deployment fixes (31 October 2026):**
  - **Plans now deliver what the pricing page promises.** A 10 GB platform cap had been limiting Pro (50 GB) and Agency (250 GB). A 500 MB file cap, 25 uploads a day and 2 active renders also applied to every plan. Each is now set per plan, and the pricing page shows them.
  - **Long quality checks survive Vercel's 120 s limit:** the request answers "still measuring" and the page keeps polling.
  - **The hourly quality-check limit is now enforced.** It was defined but never applied.
  - **The build refuses a missing or non-https `CC_API_ORIGIN` in Vercel production.**
  - **Added:** a content security policy, a root error page, `/privacy` and `/terms`, a sitemap, robots rules and a social preview image.
  - **"Talk to us" now reaches a person,** and sign-in no longer implies that a paid plan is set up by itself.
  - **The API refuses to start in production with no way to sign in,** and it hides its API explorer there.
  - **New QA:** `qa/browser_qa_stack.py` tests the browser against real object storage.

- **Sign-in fixes and white hero (31 October 2026):**
  - **Hero:** pure white, including the interactive demo.
  - **Magic links** default to the web app's address instead of the API's, and with no email provider the development sign-in page shows the link and code. Production never does.
  - **Local sign-in** no longer fails as cross-origin on `127.0.0.1`, and Google's start redirects to the configured host so its state cookie matches.
  - **The Google button** stays visible in development with setup instructions, and a stopped API now says so.
  - **`scripts/dev.ps1 all`** starts the API, a worker and the web app. It prefers the working `.venv311`.

## Simplified
- **One kind of workspace.** A workspace owns both its products and its videos. The V6–V10 split into brand and creator accounts is gone. The `users.role` column remains only for older databases.
- **One approval.** A manager approves a version after its quality check passes, attesting fit (at least 0.75). This replaces propose → creator consent → creator approval → brand approval → verify.
  - **Kept:** approval bound to the version and content hash, render gates and track integrity, signed downloads, and takedown (revoke).
  - **Lifecycle:** DRAFT → APPROVED → RENDERING → RENDERED → DELIVERED, plus REJECTED, RENDER_FAILED, REVOKED, EXPIRED and CANCELLED.
- **Sign-in is passwordless only.** Pages that used to be HTML served by the API now redirect into the app:
  - magic-link confirmation;
  - Google errors;
  - the suspended-account page.

## Removed (not needed for the MVP)
| Removed | Was |
|---|---|
| `app/commerce.py` | Creator marketplace: listings, campaigns, offers, licence grants, earnings, takedown of grants |
| `app/payments.py`, `ops/payments.py` | Supervised invoice ledger for marketplace offers. The operator metrics endpoint moved to `app/main.py` |
| Tracked links, conversion import, campaign analytics | Click and conversion attribution |
| Replicate generative-render adapter | Never verified against a live model, and its outputs could not be validated |
| Surface-proposal endpoints | Superseded by the decision engine's surface detection |
| Password register and login | Replaced by Google and the magic link or code |
| `app/static/*`, `frontend/`, `vercel.json`, `qa/vercel_emulator.py`, V6–V10 browser QA scripts | The old single-file frontend and its tooling |

The removed code is in git history (commit `eac6a91` and earlier) if the marketplace comes back.

**Data migration:** existing databases upgrade in place, and new tables and columns are added at start-up. Tables of removed features stay in older databases, unused. The compose stack was started on the V10 PostgreSQL volume and served V11 without manual steps.
