> **Superseded (V10, 30 Oct 2026):** this is the V9 report. Several items it lists as open were addressed in V10 (Neon idle polling, cache cleanup, Docker build, 3D tolerance). See [README.md](README.md) and [docs/v10/release-readiness.md](docs/v10/release-readiness.md).

# Context Commerce — Project Report (V9)

**Date:** 30 October 2026  
**Release status:** **BLOCKED** for pilot and production. Suitable for supervised demos on synthetic or self-owned footage.  
**Location:** `D:\context-commerce` (not a git repository; nothing has been committed, pushed or deployed)

---

## 1. Summary

Context Commerce lets brands place their products into creator videos with the creator's permission. The flow runs from sign-up, through the marketplace and licensing, to placement, a quality check, consent, rendering and delivery. It all works end to end on a local machine, and the automated tests pass on both supported databases.

Two things block a public launch:

1. **No real creator footage has been tested.** Every quality and accuracy number comes from synthetic test videos.
2. **The live services have never been connected:** Neon, Google sign-in, email delivery and cloud storage. They are fully coded and tested against local stand-ins, but no credentials were available. Turning them on is configuration work (section 9), not new engineering.

Section 7 recommends a stack that can run at **$0/month** to start. One code change is needed first (section 7.3).

---

## 2. What the product does

1. **Join.** A brand or creator arrives on the landing page and signs up with **Google** or an **emailed 6-digit code / magic link**, then picks a role and names a workspace, or joins a workspace they were invited to.
2. **List.** A creator uploads a video they own, the system analyses its scenes, and the creator lists it at a fixed price.
3. **License.** A brand creates a campaign, finds suitable videos in the marketplace, and sends an offer. Accepting the offer creates a licence.
4. **Place.** The brand places a product image (2D) or a 3D model (`.glb`) on a surface in the video. The surface is tracked through every frame.
5. **Check.** A quality gate checks every frame and either passes the placement, with a preview, or **refuses it with a stated reason**: for example a curved or reflective surface, motion blur, something moving in front of the product, or the product being hidden.
6. **Approve and deliver.** The creator consents to that exact version and both sides approve. The renderer then produces the video and validates it frame by frame. Downloads use short-lived, access-checked links. Revoking the licence stops everything.

---

## 3. What was implemented, and how well it is proven

**Legend**
- **Verified** — passes automated tests on both SQLite and PostgreSQL, and was exercised in a real browser against a running server.
- **Stand-in only** — the code is complete and tested, but only against a local substitute (embedded PostgreSQL, fake S3, stubbed Google, an email file outbox).
- **Synthetic only** — measured on generated test videos, not real footage.
- **Not built** — does not exist yet.

### 3.1 Accounts and sign-up (new in V9)
| Feature | Status | Notes |
|---|---|---|
| Landing page at `/`, with *Join as a brand* and *Join as a creator* | **Verified** | Desktop and 390 px mobile, checked in a browser; a mobile header bug was found and fixed |
| Email sign-in: 6-digit code | **Verified** (email written to a local outbox) | Expires after 10 min, works once, burned after 5 wrong attempts, rate-limited, doesn't reveal whether an account exists |
| Email sign-in: magic link | **Verified** (local outbox) | Opens a *Continue* page first, so email scanners can't use up the link |
| Sign in with Google | **Stand-in only** | Standard OAuth with PKCE, state and nonce, and full ID-token checks. The redirect to Google was checked in a browser; the token exchange was stubbed |
| Onboarding: choose brand or creator, name a workspace, or accept an invitation | **Verified** | |
| Password sign-in (from earlier versions) | **Verified** | |
| Real email delivery over SMTP | **Stand-in only** | Works with any SMTP provider (Gmail app password, Resend, Brevo, SendGrid, SES) |

### 3.2 Database and storage (new in V9)
| Feature | Status | Notes |
|---|---|---|
| **Neon PostgreSQL** via `DATABASE_URL`; SQLite when it isn't set | **Stand-in only** for Neon itself | The full test suite passes on PostgreSQL 16 (55/55). Never run against a real Neon project |
| One-command SQLite → PostgreSQL migration, with per-table verification | **Verified** locally | `python -m ops.dbtransfer migrate --sqlite ./data/context_commerce.sqlite3` |
| Backup and restore (both databases); refuses corrupted backups and non-empty targets | **Verified** locally | |
| Private S3-compatible storage (R2, B2, AWS S3, MinIO): write-through, fetches missing files on any server, 5-minute presigned download links | **Stand-in only** (in-process S3 mock) | |

### 3.3 Video placement and quality
| Feature | Status | Notes |
|---|---|---|
| Tier A: screen overlay | **Verified**, **Synthetic only** | |
| Tier B: tracked flat decal with colour matching | **Verified**, **Synthetic only** | Colour error (ΔE) fell from 5.50 to 4.72 over 21 clips, with 0 regressions |
| 3D product (GLB) on a tracked surface, with an estimated camera, shading and shadows | **Verified**, **Synthetic only** | Requires the real size of the surface |
| Planar quality gate | **Synthetic only** | Held-out: 42/42 correct, 0 false accepts, 0 false rejects |
| **New 3D scene check** (separates walls behind the product from things moving or standing in front of it) | **Synthetic only** | Fresh held-out: 35/36 correct, **0/18 false accepts**, 1/18 false rejects. Detects occluding objects (fired on 2 of 6 such clips; all 6 were rejected) |
| Refusing curved, reflective, blurred and occluded scenes with a reason | **Verified**, **Synthetic only** | |
| Frame-by-frame validation of every render before download | **Verified** | |
| Generative (AI) video editing | **Not built** (disabled) | No verified provider; the Replicate hook stays off |

### 3.4 Marketplace and business flow
| Feature | Status |
|---|---|
| Listings, campaigns, fixed-price offers, licences, takedown, delivery, earnings records, reports | **Verified** |
| Consent to an exact version, two-sided approval, licence revocation that stops everything | **Verified** |
| **Payments** (actually charging brands and paying creators) | **Not built** |

### 3.5 Platform and security
| Feature | Status |
|---|---|
| Durable job queue in the database (leases, heartbeat, recovery, dead-letter); inline worker or separate `worker.py` | **Verified**, including a two-process test sharing one PostgreSQL schema |
| CSRF protection, HttpOnly session cookies, rate limits, HMAC-signed links redacted from logs, cross-organisation access checks | **Verified** |
| Health endpoint that reports database and storage state without revealing hosts or bucket names | **Verified** |
| Dependency audit: `pip-audit` reports no known vulnerabilities | **Verified** (30 Oct 2026) |
| Docker image (Python 3.12, non-root, healthcheck) | **Not run** (no Docker daemon on this machine) |
| Optional ClamAV virus scanning; optional local Ollama scene descriptions | Built; not exercised in V9 |
| Independent penetration test | **Not done** |

### 3.6 Real-footage benchmark tooling (new in V9)
| Feature | Status |
|---|---|
| Consent-checked clip manifest (refuses clips without a "benchmark" consent scope or with a changed file) | **Verified** |
| Splits clips by creator, so no creator appears in both development and test data | **Verified** |
| Blind rating sheet for human raters, with anonymised file names (fixes an earlier leak of clip names) | **Verified** |
| Statistics: Wilson 95% confidence interval, Krippendorff's alpha for agreement between raters | **Verified** |
| **Run on real footage** | **Not done.** No consented footage was supplied |

---

## 4. What can honestly be called "working"

No feature is proven with real users, real footage or live cloud services. What is proven is that the system behaves correctly **on this machine**, under automated tests and in a real browser:

- **Automated tests:** 55/55 on SQLite (241 s) and 55/55 on PostgreSQL 16 (244 s).
- **Browser walkthroughs** (headless Chrome against a real server): V6 10/10, V8 6/6, V9 6/6, with no console errors. The V9 walkthrough covers:
  - landing on desktop and mobile;
  - brand sign-up by email code, then onboarding;
  - an invited teammate joining by code;
  - creator sign-up by magic link;
  - a returning user;
  - the redirect to Google.
- **Full business flow in the browser:** landing → sign-up → onboarding → marketplace → licence → placement → render → download.
- **Safety record on synthetic tests:** the quality gates **never accepted a bad placement** (0 false accepts across the planar and 3D held-out sets).
- **Clean dependency audit**, plus a secret scan of the delivery archive (0 hits).

Full evidence: [docs/v9/test-evidence.md](docs/v9/test-evidence.md).

## 5. Known limitations

- **Real footage:** none evaluated. Real noise, motion blur, rolling shutter and compression may change every quality number.
- **3D check:** no measured reduction in false rejections compared with V8 (one false reject each across 72 clips). One known cause, a fixed 1-pixel tolerance that ignores camera-pose error, is diagnosed but deliberately not fixed until new test scenes exist.
- **Live services:** Neon, Google, SMTP and the bucket are untested, and so is the Docker build.
- **Payments:** not implemented. Agency workspaces and ad-performance measurement are not built.
- **Roles:** a person can be either a brand or a creator per account, not both.
- **Local media cache:** grows without cleanup. Add a cleanup job before running on a small disk.
- **Rate limiter:** stored in the database. Fine at small scale; move it to Redis or a gateway later.

Full list: [docs/v9/known-limitations.md](docs/v9/known-limitations.md).

---

## 6. Current tech stack

| Layer | Technology | Version | Role |
|---|---|---|---|
| Language | Python | 3.11+ (Docker: 3.12) | Whole backend |
| Web framework | FastAPI on Starlette, served by Uvicorn | FastAPI 0.162, Uvicorn 0.48 | API, pages, auth routes |
| Frontend | Plain HTML, CSS and JavaScript (no framework, no build step) | — | Landing page (`app/static/landing.html`) and app (`app/static/index.html`) |
| Database | **Neon PostgreSQL** (production) or SQLite (local fallback) | psycopg 3 + psycopg-pool | One SQL dialect, translated automatically for PostgreSQL (`app/db.py`) |
| Object storage | Any S3-compatible bucket (Cloudflare R2, Backblaze B2, AWS S3, MinIO) | boto3 | Uploads, renders, frames, analyses |
| Authentication | Own implementation: Google OpenID Connect (PKCE) + email code / magic link + passwords | — | `app/auth.py` |
| Email | SMTP to any provider, or a file outbox in development | Python `smtplib` | `app/mailer.py` |
| Computer vision | OpenCV (headless) | 4.11 | Tracking, optical flow, camera solving |
| Image processing | Pillow | 13 | Image decoding |
| Video | FFmpeg / FFprobe | 8.1 tested | Decoding and the final encode (audio kept) |
| 3D rendering | Own software rasteriser (no GPU needed) | — | GLB models, shading, shadows (`app/render3d.py`) |
| Background jobs | Job queue in the database, with leases | — | Inline thread or separate `worker.py` |
| Packaging | Docker (Python 3.12-slim, non-root), docker-compose | — | Not yet built on this machine |
| Testing | pytest, pgserver (embedded PostgreSQL 16), moto (fake S3), headless Chrome | — | Dev-only (`requirements-dev.txt`) |
| Security tooling | pip-audit | — | Dependency check |
| Optional | ClamAV (virus scan), Ollama (local vision descriptions), Replicate (generative, disabled) | — | Off unless configured |

```
Browser (landing page + app, plain HTML/JS)
   │  HttpOnly session cookie + CSRF token
FastAPI (app/main.py) ── auth.py (Google, email code/link, onboarding)
   │                  ── commerce.py (marketplace, offers, licences)
   │                  ── quality.py + tracking.py + render3d.py (quality gates)
   ├── Database: Neon PostgreSQL (or SQLite) ── users, licences, jobs queue, rate limits
   ├── Object storage: S3-compatible private bucket ── videos, renders (presigned downloads)
   ├── Email: SMTP provider ── sign-in codes and links
   └── Worker (thread or worker.py) ── claims jobs → FFmpeg render → frame-by-frame validation
```

---

## 7. Recommended $0 starting stack

### 7.1 Should the stack change?

**No rewrite is needed.** The current stack (FastAPI + Neon + S3-compatible storage + SMTP + Google sign-in) is already built from services with free tiers, and it is the tested code. Swapping in Supabase, Clerk, Next.js or Firebase would throw away working, tested code and gain nothing at this stage.

What matters is **where it runs**. This app renders video with FFmpeg and OpenCV, which needs real CPU, at least 1–2 GB of RAM, and a worker that can run for minutes. Most free hosting gives 512 MB and 0.1 CPU and sleeps when idle, which is not enough.

### 7.2 Recommended $0 setup

Free-tier limits change often. The figures below come from searches on 30 October 2026, and several sources disagreed, so **check each provider's pricing page before relying on them.**

| Need | Recommended free option | Free allowance (approx.) | Why it fits | Watch out for |
|---|---|---|---|---|
| **App server + worker** | **Oracle Cloud Always Free**, Ampere A1 Arm VM, running `docker compose` | Reported as **2 OCPU / 12 GB RAM** since mid-2026 (older docs say 4 OCPU / 24 GB) | The only free option with enough CPU and RAM for video rendering and an always-on worker | A card is needed to sign up. The Arm image build is expected to work (all dependencies publish Arm wheels) but is **untested**. Idle free VMs can be reclaimed |
| **Database** | **Neon Free** (already integrated) | 100 CU-hours of compute per project per month; scales to zero after 5 min idle; storage 0.5–1 GB per project (sources disagree) | Already built and tested; branching is useful for restore drills | **Needs the code change in 7.3**, or compute is suspended partway through each month |
| **Video storage** | **Cloudflare R2** (S3-compatible, already supported) | 10 GB storage, 1M write and 10M read operations per month, **free egress** | Free downloads matter for video; just set `CC_S3_ENDPOINT_URL` | 10 GB is about 50–100 source videos plus renders. Backblaze B2 is an alternative |
| **Sign-in email** | **Gmail with an app password** to start, then **Resend** or **Brevo** | Resend: 100/day, 3,000/month, 1 domain. Brevo: 300/day | Plain SMTP, so switching provider is just a change in `.env` | Resend and Brevo need a domain you own for good deliverability. Codes sent from a Gmail address may land in spam |
| **Google sign-in** | Google Cloud OAuth client | Free | Already built | While the consent screen is in *Testing*, only listed test users can sign in. Publish it before launch; the app only asks for `openid email profile` |
| **HTTPS + domain routing** | **Cloudflare Tunnel** in front of the VM | Free | HTTPS with no open ports on the server, and it hides the server's IP | Needs a domain on Cloudflare (the temporary `trycloudflare.com` URLs are for testing only) |
| **Code hosting + CI** | Private GitHub repository + GitHub Actions | Free monthly minutes for private repos | Runs the test suite on every change | The project isn't a git repository yet |
| **Uptime alerts** | Any free uptime monitor | Free plans exist | Alerts you if the site goes down | Point it at `/` rather than `/api/health`, which wakes the database (see 7.3) |

**The one realistic cost is a domain name.** It's needed for trustworthy sign-in emails and a proper HTTPS address, and costs roughly $10 a year. Everything else can start at $0.

**Alternative app hosts:**
- **Hugging Face Spaces (Docker, free CPU):** 2 vCPU / 16 GB, sleeps after 48 h without visitors. Good for **demos**. Check the visibility settings so your code isn't public.
- **Google Cloud Run:** a monthly free allowance (180,000 vCPU-seconds, 360,000 GiB-seconds, 2M requests on request-based billing). A poor fit for now, because renders run after the web request ends. It would need the worker moved to Cloud Run Jobs.
- **Not recommended for this app:**
  - **Render free** and **Koyeb free**: 512 MB RAM, 0.1 CPU, sleep when idle, no background workers.
  - **Vercel and Netlify**: serverless functions can't run long FFmpeg jobs. They are fine for hosting just the landing page.

### 7.3 Code changes needed before running on Neon Free

**Required: stop keeping Neon awake.** The background worker checks the database for new jobs **every 0.5 seconds** (`_worker_loop` in `app/main.py`), and the Docker healthcheck pings the database every 30 seconds. Neon's compute therefore never scales to zero. Even at the smallest size (0.25 CU), the 100 free CU-hours run out after about **400 hours, roughly day 16–17 of each month**, and the database is then suspended until the next month. Fixes:
1. **Worker:** back off when idle (for example 0.5 s → 60 s), and wake immediately when the web process queues a job.
2. **Healthcheck:** check the web process only, and check the database less often.
3. **Connection pool:** close idle connections, then confirm in Neon's dashboard that compute actually suspends.

**Recommended:**
- Add a cleanup job for the local media cache (the Oracle VM's disk is limited).
- Keep `CC_SIGNING_KEY` identical on every server.

These changes have **not** been made. I can make them on request.

### 7.4 What will cost money first, as you grow
| Limit you'll hit | Typical next step |
|---|---|
| More than about 100 sign-in emails a day | Resend Pro or a paid Brevo plan |
| More than 10 GB of video | R2 at about $0.015 per GB-month (still free downloads) |
| Neon compute or storage limits | Neon's usage-based paid plan |
| Rendering queue too slow on 2 OCPU | A paid VM or more worker machines |
| Taking money | Stripe or Razorpay (no monthly fee, per-transaction charges). Payments are **not built yet** |

---

## 8. How to run it locally
```bash
python -m venv .venv && .venv\Scripts\Activate.ps1      # Python 3.11+ and FFmpeg on PATH
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env                                     # fill in what you use
uvicorn app.main:app --reload                              # http://127.0.0.1:8000
PYTHONPATH=. python -m pytest -q                           # 55 tests
```

## 9. Go-live checklist (platform layer)
1. **Neon:** create a project and put its connection string in `DATABASE_URL`. Run `python -m ops.dbtransfer migrate --sqlite ./data/context_commerce.sqlite3` if you have local data.
2. **Google:** create an OAuth client of type *Web application*, with redirect URI `<CC_PUBLIC_URL>/auth/google/callback`. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
3. **Email:** set the `CC_SMTP_*` variables and `CC_MAIL_FROM`.
4. **Storage:** set `CC_S3_BUCKET`, the access keys, and `CC_S3_ENDPOINT_URL` for R2.
5. **Server:** set `CC_PUBLIC_URL`, a long random `CC_SIGNING_KEY`, and `CC_SECURE_COOKIES=1` behind HTTPS.
6. **Checks:** run the five checks in [docs/v9/release-readiness.md](docs/v9/release-readiness.md): health, Google and email sign-up, media upload and download, a backup restore into an empty Neon branch, and `docker compose up --build`.

## 10. Next three actions
1. **Real-footage benchmark:** 50–100 consented clips from at least 10 creators, rated blind by 3 or more people. This is the main release blocker.
2. **Verify the live platform** on the $0 stack above, after the Neon fix in 7.3.
3. **Fix the 3D tolerance issue** (scale it with camera-pose error) and validate the fix on new test scenes, then on real footage.

---

## Sources for free-tier figures (checked 30 Oct 2026; verify before relying on them)
- Neon: [Free plan limits FAQ](https://neon.com/faqs/free-plan-limits-and-quotas), [usage-based pricing](https://neon.com/blog/new-usage-based-pricing)
- Cloudflare R2: [pricing](https://developers.cloudflare.com/r2/pricing)
- Resend: [pricing](https://www.resend.com/pricing.md), [new free tier](https://resend.com/blog/new-free-tier.md)
- Brevo: [free plan review](https://www.emailtooltester.com/en/reviews/brevo/), [Brevo help: email quotas](https://help.brevo.com/hc/en-us/articles/8292912279954-Add-or-remove-emails-from-your-plan)
- Oracle Cloud: [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm), [report of the 2026 reduction](https://linuxiac.com/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/)
- Google Cloud Run: [pricing](https://cloud.google.com/run/pricing)
- Hugging Face Spaces: [hardware docs](https://huggingface.co/docs/hub/en/spaces-gpus), [managing Spaces](https://huggingface.co/docs/huggingface_hub/guides/manage-spaces)
- Render and Koyeb: [Koyeb instances](https://www.koyeb.com/docs/reference/instances), [Koyeb pricing FAQ](https://www.koyeb.com/docs/faqs/pricing), [Render free tier summary](https://freetier.co/directory/products/render)
