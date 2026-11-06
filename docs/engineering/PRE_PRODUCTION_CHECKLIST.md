# Before production: the owner's checklist

**As of 6 November 2026, after M13 to M17.** The code is complete as far as it can be without your accounts, and it passes everything that runs on this machine. What's left needs you: credentials, accounts, a push, a lawyer, and decisions about money.

Do the steps in order. Each says what to do, how to check it worked, and what it unblocks. Commands run from the repository folder.

## Where things stand
| | State |
|---|---|
| **Release status** | **NOT READY FOR RELEASE**, until steps 1–6 are done |
| **After steps 1–6** | Fit for a **controlled beta**: invited customers, bank transfer or Stripe *test* payments |
| **After steps 1–9** | Fit for **production**, with live payments |

---

## Step 1. Reset the Neon database password (P0, do this first)
The current password is in public git history (commit `c37197a`). Anyone can read it.
1. Neon console → your project → **Roles** → the role in your connection string → **Reset password**.
2. Copy the new **pooled** connection string. It ends with `?sslmode=require`.
3. Put it in `.env.vercel` as `DATABASE_URL`. That file is ignored by git; never commit it.
4. Check:
   ```
   python qa/check_env.py
   ```
   It must say `DATABASE_URL password never committed: OK` and `DATABASE_URL encryption: OK`.

Detail: [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md) §1. Rewriting git history is optional once the password is reset, and it's your decision: it needs a force push.

## Step 2. Commit everything together and push
1. Review the changes (`git status`, `git diff`). Everything from M13 to M17 belongs together; don't commit part of it.
2. Make sure `.env`, `.env.vercel` and `models/*.onnx` are **not** staged. `git status` must not list them; they're ignored.
3. Commit, then push to `main`.
4. Check: GitHub → **Actions** → the `ci` run. Every job must pass:
   - lint;
   - tests on SQLite and on PostgreSQL;
   - dependency and secret scans;
   - the web checks;
   - the image build, its smoke test, and the container vulnerability scan.

**If the container scan fails,** it lists packages with a fixed CRITICAL or HIGH vulnerability. Usually a rebuild picks up the fix; otherwise update the package in `requirements.txt` or the base image. Don't disable the scan.

**If any test fails in CI and not here:** CI uses Python 3.12 on Ubuntu, while this machine uses Python 3.11 on Windows. Send me the failing job's log.

CI also downloads the detection model from github.com, which needs network access.

## Step 3. Set up the accounts and settings
**Vercel plan.** The free plan forbids commercial use (B-5): move the team to **Vercel Pro** before you charge anyone.

**Settings.** Vercel → your project → **Settings → Environment Variables** → *Import .env*, choose `.env.vercel`, scope **Production**.

| Setting | Value or source |
|---|---|
| `DATABASE_URL` | Step 1 |
| `CC_SIGNING_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"`, once; keep it secret. Changing it signs everyone out |
| `CC_PUBLIC_URL` | `https://www.visuara.si` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud console |
| `CC_SMTP_HOST`, `CC_SMTP_PORT`, `CC_SMTP_USER`, `CC_SMTP_PASSWORD`, `CC_SMTP_TLS=starttls`, `CC_MAIL_FROM` | Your email provider |
| `CC_OPS_TOKEN` | 32 or more random characters, for monitoring |
| `BLOB_READ_WRITE_TOKEN` | Added by Vercel when you connect a Blob store |
| `NEXT_PUBLIC_CONTACT_EMAIL`, `NEXT_PUBLIC_LEGAL_NAME`, `NEXT_PUBLIC_LEGAL_JURISDICTION` | Your business details, shown on the legal pages |

**Google sign-in.** In Google Cloud → APIs & Services → Credentials → your OAuth client, add exactly `https://www.visuara.si/auth/google/callback` (B-3).

**Email.** Gmail's SMTP works for testing but limits how many messages you can send a day. For real customers, use a transactional email provider and set up SPF and DKIM for `visuara.si`, so sign-in emails don't land in spam. Test it:
```
python qa/check_env.py --mail you@example.com
```

**Check the whole file:**
```
python qa/check_env.py
```
Every line must be OK. The API refuses to start in production while anything is unsafe, and Vercel's logs say what.

## Step 4. Deploy to a preview first, then production
1. Push to a branch, so Vercel builds a **preview** deployment.
2. Check the preview:
   ```
   python qa/vercel_check.py <preview URL>
   ```
   It checks:
   - health, including `object_detection.installed: true`;
   - security headers, cookies and HTTPS;
   - Google sign-in.
3. **Optional (D-SEC-07):** tell me whether the preview's API container receives a `PORT` setting. If it does, the containers can run as a non-root user; that's the one open security item.
4. **Back up first.** In Neon, create a branch of the production database (it's instant), or export it: `DATABASE_URL=… python -m ops.dbtransfer export --out ./backups/db-2026-11-06`. Migrations run automatically at start-up: `0016` to `0019` add tables and columns only.
5. Promote to production. Check again:
   ```
   python qa/vercel_check.py https://www.visuara.si
   ```
6. Sign up yourself, with email and with Google. Upload a short video you own, and watch an analysis complete.

## Step 5. Turn on monitoring
- **Uptime:** point an uptime monitor at `https://www.visuara.si/api/health/live`, every minute. It never touches the database.
- **Operator checks:** schedule `CC_OPS_TOKEN=… python -m ops.monitor --api https://www.visuara.si --webhook <your chat webhook>` every 30–60 minutes (runbook §10). It alerts on:
  - the API being down;
  - the job queue;
  - failure rates;
  - an unbalanced ledger;
  - payment events that need a person.
- **Make yourself the platform administrator:** with `DATABASE_URL` set to production, run `python -m ops.platform_admin grant you@example.com` (after you've signed up). Administrators verify rights evidence, see payment events and record bank transfers.

## Step 6. Payments in test mode (for the beta)
Choose one:
- **Bank transfer only.** No provider needed. Invoices are paid by transfer and you record them in Admin → Invoices (runbook §8d).
- **Stripe test mode.** Create a Stripe account, take the test keys and set up the webhook (runbook §8a). Run one invoice through with the test card and confirm:
  - the invoice turns PAID;
  - one `APPLIED` payment event;
  - a balanced trial balance;
  - a re-sent event changes nothing.

  This is the one piece of payments I couldn't verify myself (B-4).

**Controlled beta can start here.** Invite a small number of publishers and brands you know, tell them it's a beta, and watch the monitor and Vercel's logs.

## Step 7. Legal review (before any real customer money, B-8)
A lawyer should review:
- the terms of service and privacy policy (the pages under `/legal`);
- video licensing: publishers confirm they own their footage or are licensed to edit it, and the rights evidence process;
- the brand–publisher contract the quotes and invoices stand for, and the platform fee (20% by default, `CC_PLATFORM_FEE_BPS`);
- data protection: where data is stored (Neon, Vercel Blob regions), a data processing agreement, how a user's data is deleted;
- payment terms, refunds, invoicing and VAT for your jurisdiction;
- brand-safety commitments: what Visuara promises, and what it only flags for a person.

## Step 8. Evaluate on real footage (before claiming accuracy, B-7)
Every accuracy figure so far comes from real photographs or synthetic video. Before telling customers how accurate detection, tracking or surface placement is:
1. Collect 20–50 short clips with **written permission** to use them for evaluation. They should vary: indoor and outdoor, handheld and static, different lighting.
2. Label them following [benchmark/real/ANNOTATION_GUIDE.md](../../benchmark/real/ANNOTATION_GUIDE.md): two people label each clip independently, before any run.
3. Run `python -m benchmark.real_footage validate --manifest benchmark/real/manifest.json`, then `python -m benchmark.real_footage run --manifest benchmark/real/manifest.json --split test --out benchmark/real/runs/first`, and the tests in `tests/test_real_footage.py`. Then send me the results to review and record in [AI_EVALUATION.md](AI_EVALUATION.md).

Until then, describe scores as "ranking aids", as the product already does.

## Step 9. Live payments (your decision)
Only after steps 6 and 7, and with Vercel Pro:
1. In Stripe, switch to live mode. Create a live webhook endpoint (the same URL and events) and copy the live keys.
2. In Vercel set `CC_STRIPE_SECRET_KEY` (`sk_live_…`), `CC_PAYMENT_WEBHOOK_SECRET` (the live webhook's secret) and **`CC_PAYMENT_LIVE=1`**. That last setting is your explicit authorisation; without it, a live key is refused.
3. Make one small real payment yourself and refund it in Stripe. Check that the ledger and the payment events are right.
4. Reconcile daily for the first weeks (runbook §8c).

---

## Go / no-go
| Check | Needed for beta | Needed for production |
|---|---|---|
| Neon password reset (step 1) | ✔ | ✔ |
| CI green on `main` (step 2) | ✔ | ✔ |
| Settings imported, Google URI added, email working (step 3) | ✔ | ✔ |
| `qa/vercel_check.py` passes on production (step 4) | ✔ | ✔ |
| Monitoring scheduled (step 5) | ✔ | ✔ |
| Payments: bank transfer, or Stripe test mode verified (step 6) | ✔ | ✔ |
| Vercel Pro | ✔ (before charging anyone) | ✔ |
| Legal review (step 7) | Recommended | ✔ |
| Real-footage evaluation (step 8) | — | ✔ before accuracy claims |
| Live payments (step 9) | — | ✔ if you take cards |

## What customers should know in the beta
- Detection recognises everyday objects (80 kinds) and not brands or logos. A scene's "setting" can be wrong.
- Scores and matches are ranking aids, not predictions of campaign results.
- No vision model is configured, so scenes have no written description.
- Payouts to publishers are made by bank transfer and recorded by Visuara.
- Programmatic channels (SSP, DSP, exchanges) are not connected.

Full detail: [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md), [FINAL_RELEASE_REPORT.md](FINAL_RELEASE_REPORT.md), [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md).
