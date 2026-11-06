# Deployment guide

The platform runs as one Vercel project with three services (`web`, `api`, `runner`). The step-by-step setup is in [docs/vercel.md](../vercel.md). This page adds what changed with Phases 1 to 15 and the order of operations for a release.

## Go-live checklist
Do these in order. Steps 1 to 4 need the owner's accounts; nothing in the repository can do them.

1. **Replace the leaked database password (B-1).** In Neon, open the project, then Roles, and reset the password of the role in `DATABASE_URL`. Copy the new pooled connection string (its host contains `-pooler`) into `.env.vercel`. The old password is in commit `c37197a` on GitHub, so it is public whatever happens to the history.
2. **Make the GitHub repository private** (Settings, then Danger zone). It is optional once step 1 is done, but it stops the history being read.
3. **Fill `.env.vercel`** (it stays on your machine; git ignores it). Required:
   - `DATABASE_URL` (from step 1), `CC_SIGNING_KEY`, `CC_PUBLIC_URL` (`https://…`) and `BLOB_READ_WRITE_TOKEN`.
   - A way to sign in: Google (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) or email (`CC_MAIL_BACKEND=smtp` with the `CC_SMTP_*` settings and `CC_MAIL_FROM`).
   - See [docs/vercel.md](../vercel.md) step 1.
4. **Check it:** `python qa/check_env.py`. It never prints a value. It refuses:
   - a short signing key or a non-HTTPS address;
   - a database it cannot reach, or a Google or SMTP login that fails;
   - **any secret that appears anywhere in the git history**, which is how it confirms step 1 was done.

   Fix whatever it marks `FIX`.
5. **Import the settings into Vercel (B-2):** Settings, then Environment Variables, then Import .env, for **Production**, and choose `.env.vercel`.
6. **Allow Google's return address (B-3):** in Google Cloud Console, open the OAuth client and add `https://<your domain>/auth/google/callback` exactly. `qa/check_env.py` checks it.
7. **Migrate before the new containers start:** `DATABASE_URL=… python -m ops.migrate`.
   - It applies the frozen baseline and then migrations 0001 to 0015 in order, each recorded in `schema_migrations`.
   - Deferred foreign keys are checked after each migration on PostgreSQL.
   - It stops on a migration that was changed after release, or one this code does not know.
   - All migrations are additive. The API and runner also migrate at start-up under an advisory lock, but running it first means a failure stops the release, not the containers.
8. **Check for secrets, then push:** `python qa/secret_scan.py` must report 0 findings. Then commit and push to the production branch. Vercel builds `web`, `api` and `runner` from `vercel.json`.
9. **Verify the live site:**
   - `GET /api/health/ready` answers `ready`.
   - Sign in with Google and with an email code.
   - On the welcome page, choose what the workspace is (brand, publisher, agency or just your own videos).
   - Analyse a short video and see it complete; on Vercel the HTTP job runner runs the stages.
10. **Grant the first platform administrator** from a machine with `DATABASE_URL`: `python -m ops.platform_admin grant person@example.com`. Administrators verify rights evidence and record payments. Without one, nothing can be listed or paid.

## New settings (all optional)
| Variable | Default | Meaning |
|---|---|---|
| `CC_PLATFORM_FEE_BPS` | 2000 | Platform fee in basis points |
| `CC_RESERVATION_HOLD_SECONDS` | 900 | How long a reservation is held |
| `CC_REQUEST_RESPONSE_SECONDS` | 259200 | How long the publisher has to answer a request |
| `CC_HASH_BACKFILL_PER_RUN` | 2 | Older videos hashed per maintenance run |
| `CC_ANALYSIS_SAMPLE_SECONDS`, `CC_ANALYSIS_CUT_THRESHOLD`, `CC_ANALYSIS_MIN_SCENE_SECONDS`, `CC_ANALYSIS_FRAMES_PER_SCENE`, `CC_ANALYSIS_VISIBILITY_STEP_SECONDS`, `CC_ANALYSIS_TRACK_MIN_IOU`, `CC_ANALYSIS_MAX_SURFACES_PER_SCENE`, `CC_ANALYSIS_CUT_GAIN_NORMALISE` (1 or 0) | see `intelligence.DEFAULT_CONFIG` | Analysis configuration, stored with every analysis version |
| `CC_VISION_MODEL` | unset | Local vision model for scene descriptions; without it, brand safety needs a person's review |

## Free-plan limits that apply
- **Functions** run for at most 300 s with 1 vCPU and 2 GB. An analysis that doesn't finish in one request resumes in the next, without repeating finished stages.
- **No commercial use (B-5):** charging real customers needs Vercel Pro.

## Demonstration environment
`python -m ops.demo reset --data ./demo-data` builds a complete marketplace on a local SQLite file. It refuses to run when `DATABASE_URL` is set. Start the API with `CC_DATA_DIR=./demo-data` and the web app as usual.

## Rollback
- **Code:** redeploy the previous build.
- **Database:** additive migrations leave the older code working against a newer schema, with one exception. Older code refuses a database that has migrations it doesn't know, and says so, which keeps it from writing data it doesn't understand. To roll back across a migration, restore the Neon branch from before the release (point-in-time restore) or a logical dump (`python -m ops.dbtransfer`).
