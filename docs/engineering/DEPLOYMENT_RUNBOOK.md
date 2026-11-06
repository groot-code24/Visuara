# Deployment and operations runbook

For the owner and whoever runs Visuara in production. Each procedure says who can do it, what it needs, and how to tell it worked. Click-by-click Vercel and Neon screens are in [docs/vercel.md](../vercel.md); this page gives the order and the checks.

**Rules that hold throughout**
- Never paste a secret into a chat, an issue, a commit or a log. `.env.vercel` is ignored by git; keep it that way.
- Every script below prints setting names and results, never values.
- Production changes (credentials, the database, domains, payments) are the owner's, and each one is confirmed before it's done.

---

## 1. Rotate the exposed Neon credential (B-1, do this first)

**Why:** the role password in commit `c37197a` (`.env.vercel.example`, line 6) is on GitHub, so it has to be treated as known to anyone. A clean working tree doesn't change that, and neither would rewriting history. Only a new password ends the exposure.

| Step | Who | What | Check |
|---|---|---|---|
| 1 | Owner | Neon console → project → **Roles** → the role in the connection string → **Reset password** | Neon shows a new connection string |
| 2 | Owner | Copy the **pooled** string (its host contains `-pooler`). It ends with `?sslmode=require`; keep that | — |
| 3 | Owner | Put it in `.env.vercel` as `DATABASE_URL` (the file is git-ignored) | `git status --short` doesn't list `.env.vercel` |
| 4 | Owner | `python qa/check_env.py` | `OK DATABASE_URL password never committed`, `OK DATABASE_URL encryption`, `OK Neon connection` |
| 5 | Owner | Vercel → project → **Settings → Environment Variables** → replace `DATABASE_URL` for **Production** (and Preview if set) → **Redeploy** | `python qa/vercel_check.py https://<domain>` reports the database reachable |
| 6 | Owner | Neon → **Monitoring**: look for connections you can't explain from before the reset | — |
| 7 | Owner | Make the GitHub repository private | — |

**Optional, owner's decision:** rewriting history to remove `c37197a` needs a force push and breaks every existing clone. It doesn't make the old password secret again, so it is never a substitute for steps 1 to 5.

**The same procedure for any other secret:** `CC_SIGNING_KEY`, `GOOGLE_CLIENT_SECRET`, `CC_SMTP_PASSWORD`, `BLOB_READ_WRITE_TOKEN`, `CC_OPS_TOKEN`. Replace it at its service, update `.env.vercel`, run `qa/check_env.py`, then update Vercel and redeploy. Rotating `CC_SIGNING_KEY` signs everyone out and invalidates unexpired download links; that's expected.

## 2. First production deployment

1. **Settings.** Fill `.env.vercel` from [.env.example](../../.env.example) and [docs/vercel.md](../vercel.md):
   - `DATABASE_URL` with `?sslmode=require`;
   - `CC_SIGNING_KEY` (48 random characters);
   - `CC_PUBLIC_URL=https://www.visuara.si`, exactly the address users type, and the one Google returns to;
   - SMTP: `CC_MAIL_BACKEND=smtp`, `CC_SMTP_HOST`, `CC_SMTP_PORT`, `CC_SMTP_TLS=starttls` or `ssl`, `CC_SMTP_USER`, `CC_SMTP_PASSWORD`, `CC_MAIL_FROM`;
   - `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`;
   - `BLOB_READ_WRITE_TOKEN` (connect a private Blob store);
   - `NEXT_PUBLIC_CONTACT_EMAIL`.
2. **Check:** `python qa/check_env.py --mail <your address>`. Every line must say `OK`, and the test email must arrive.
3. **Google:** in the OAuth client, add exactly `https://www.visuara.si/auth/google/callback` as an authorised redirect URI (B-3). The address the API sends is `CC_PUBLIC_URL + /auth/google/callback`, so the two must match character for character. If people can also reach `https://visuara.si`, redirect it to `www` in Vercel's domain settings. Otherwise Google's sign-in cookie is set on one host and returns to the other.
4. **Database:** `DATABASE_URL=… python -m ops.migrate`. It prints `schema ok: engine=postgres neon=True …`. A failed migration stops here, before any container serves traffic.
5. **Import and deploy:** Vercel → Environment Variables → import `.env.vercel` for **Production**, then redeploy (B-2).
6. **Verify:** `python qa/vercel_check.py https://www.visuara.si`. Then sign in by email and by Google yourself.
7. **Administrator:** `DATABASE_URL=… python -m ops.platform_admin grant <your email>`. There's no web path to this role.

**If the API doesn't start,** Vercel's log for the `api` service has one line, `Refusing to start with CC_ENV=production:`, followed by every problem. A missing `DATABASE_URL` stops it there; production never falls back to SQLite.

## 2a. The object-detection model
- **The image fetches it.** `Dockerfile.vercel` runs `python ops/fetch_models.py` at build time. It downloads YOLOX-Tiny (19 MB) from the YOLOX GitHub release and installs it only if its SHA-256 matches the one pinned in `app/detection.py`. A build without network access to github.com fails, which is intended.
- **If detection is missing,** the analysis's DETECTING_OBJECTS stage reports `UNAVAILABLE` with the reason, and analyses still complete. Check with `GET /api/analyses/{id}` (its `model_versions`).
- **Settings, all optional:**
  - `CC_DETECTION_MODEL`: a key from `app/detection.py`, or `none`;
  - `CC_DETECTION_MIN_CONFIDENCE`: default 0.35;
  - `CC_ANALYSIS_DETECTION_MAX_FRAMES`: default 240;
  - `CC_ANALYSIS_DETECTION_STEP_SECONDS`: default 0.5.
  A different model must go into the image too: `python ops/fetch_models.py <name>`.
- **Vision model, optional:** set `CC_VISION_MODEL` and `CC_OLLAMA_URL` to a separately run Ollama-compatible server. Without them scenes have no description, and that's reported, not hidden.

## 3. Routine deployment

1. CI is green on the commit: `lint`, `test-sqlite`, `test-postgres`, `security`, `web`, `image`.
2. When the commit adds migrations, run `python -m ops.migrate` against production first.
3. Deploy (Vercel deploys `main`). Then run `python qa/vercel_check.py https://www.visuara.si`.

## 4. Roll back

- **Code:** Vercel → Deployments → the last good deployment → **Promote to Production**. That's instant, and nothing else is needed when the bad release added no migration.
- **After a migration:** migrations only add tables, columns and indexes, so the previous code runs on the newer schema. Roll the code back and leave the schema alone. Never edit or delete an applied migration; fix forward with a new one.
- **Data:** see §5.

## 5. Back up and restore

| Layer | First line | Second line |
|---|---|---|
| Database | Neon point-in-time restore (to a new branch, then switch `DATABASE_URL`) | `python -m ops.dbtransfer export --out ./backups/db-YYYY-MM-DD` (one consistent snapshot, SHA-256 manifest) |
| Media | Blob store (enable versioning where the store offers it) | `python ops/backup.py backup --data ./data --out ./backups/YYYY-MM-DD` on a host holding the media |

**Restore drill** (outside production, at least once a quarter):
1. Create an empty Neon branch, or a local PostgreSQL (`docker compose up -d postgres`).
2. `DATABASE_URL=<empty target> python -m ops.dbtransfer import --dump ./backups/db-…`. It verifies every hash, refuses a target that already holds data, then compares row counts and checksums table by table.
3. Point a local API at it and sign in.

`tests/test_dbtransfer.py` runs the same round trip on every CI run, including between SQLite and PostgreSQL.

## 6. Background work and worker recovery

- Analyses, decisions and renders are leased (`lease_until`). A runner that dies stops renewing, and the next run requeues the job. After the retries run out, the job is dead-lettered with its reason.
- Nothing to do by hand after a crash: the next request to the runner, or `POST /api/internal/run-jobs` from the API, resumes the work.
- **Stuck queue:** `CC_OPS_TOKEN=… python -m ops.monitor --api https://www.visuara.si` shows queue depth, the oldest job's age, worker heartbeats and the 24-hour failure rate. Exit code 1 means it alerted.

## 7. Incident response

1. **Contain:** for a leaked secret, rotate it (§1). For a bad deployment, roll back (§4). For abuse from one account, suspend it in Organizations, or `python -m ops.platform_admin` for platform roles.
2. **Find out:** Vercel logs are JSON with `request_id` and `job_id`. A user quotes the request id shown on error pages. Logs never contain passwords, tokens, signed links, sign-in codes, email addresses or configured secrets (`tests/test_platform.py::test_logs_never_contain_credentials`).
3. **Record:** what happened, when it was noticed, what was exposed, and what changed. Add a regression test for the cause.

## 8. Payments

### 8a. Turn on Stripe in test mode first
1. **Get test keys.** In Stripe, with **Test mode** on, copy the secret key (`sk_test_…`).
2. **Create the webhook endpoint.** Under Developers → Webhooks, add an endpoint:
   - URL: `https://www.visuara.si/api/payments/webhooks/stripe`;
   - events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`.
   Copy its signing secret (`whsec_…`).
3. **Add the settings in Vercel** (Production):
   - `CC_PAYMENT_PROVIDER=stripe`;
   - `CC_STRIPE_SECRET_KEY` = the `sk_test_` key;
   - `CC_PAYMENT_WEBHOOK_SECRET` = the `whsec_` secret.
   Redeploy. The API refuses to start if they're inconsistent; `GET /api/payments/config` shows `mode: test, ready: true`.
4. **Pay a real test invoice end to end:**
   1. The brand presses **Pay** in Finance.
   2. On Stripe's page, pay with the test card `4242 4242 4242 4242`.
   3. The invoice turns PAID, and `GET /api/admin/payments/events` shows one `APPLIED` event.
   4. `GET /api/admin/ledger/trial-balance` reports `balanced: true`.
   5. In Stripe, resend the same event: Visuara answers with the first result and the ledger doesn't change.
5. **Check the signature path** by sending an event from Stripe's dashboard with a wrong endpoint secret: it must be refused (400).

### 8b. Going live (the owner's decision)
Live payments need, all of them:
- Vercel Pro (B-5);
- the legal review (B-8);
- the steps in 8a passing in test mode;
- the owner's explicit choice, by setting `CC_PAYMENT_LIVE=1` together with the `sk_live_` key and the live webhook's secret.

Without `CC_PAYMENT_LIVE=1`, a live key is refused at start-up.

### 8c. Daily reconciliation
- **Find what needs a person.** `python -m ops.monitor` alerts when payment events need one; they're listed at `GET /api/admin/payments/events?result=…`:
  - `MISMATCH`: the amount, currency or invoice differs from the checkout. Nothing was applied; find out why in Stripe before doing anything.
  - `ALREADY_PAID`: a second successful payment for a paid invoice. **Refund it in Stripe.** The ledger was not changed.
  - `UNKNOWN_CHECKOUT`: a payment for a checkout Visuara didn't create. Investigate.
- **Tally the books.** The sum of `APPLIED` events for the day equals Stripe's balance transactions for the day. The trial balance must be balanced.
- **Payouts to publishers** stay manual: Admin → Allocations, with the bank reference and an `Idempotency-Key`.

### 8d. Bank transfers (always available)
A platform administrator marks an invoice paid with the bank reference (Admin → Invoices) and an `Idempotency-Key`. Then check that `GET /api/admin/ledger/trial-balance` reports `balanced: true`. A second attempt with the same key and a different reference is refused, so a payment can't be recorded twice. A bank transfer and a provider payment post the same ledger transaction; whichever arrives second is not applied.

## 9. Agencies
A brand's owner or admin gives an agency access in **Organizations → Agency access**, with the agency's id, which the agency sees on its own Organizations page. The grant's role is viewer, editor or manager. Revoking takes effect on the agency's next request. Every action an agency person takes is recorded under that person, and the `organization.switched` event says which agency they came through.

## 10. Monitoring schedule
- **Every minute or so:** an external uptime monitor on `https://www.visuara.si/api/health/live`. It never touches the database.
- **Every 30 to 60 minutes:** `CC_OPS_TOKEN=… python -m ops.monitor --api https://www.visuara.si --webhook <chat webhook>`, from a scheduler you control (a GitHub Actions schedule works). It alerts on:
  - the API being down or not ready;
  - the job queue backlog and age;
  - job and analysis failure rates;
  - disk;
  - an unbalanced ledger;
  - payment events that need a person.
- **Capacity:** one API instance on one vCPU served 59 requests a second for 20 concurrent users, with no errors (p95 542 ms, p99 663 ms; `qa/load_test.py --docker`). Vercel adds instances under load. Watch p95 in Vercel's observability, and the database's connection count on Neon.
