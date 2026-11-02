# Deploy Visora on Vercel

Everything runs in one Vercel project: the website, the API and the video jobs. You also need Neon for the database, a Google OAuth client for Google sign-in, and an email account for magic links. No server, no Docker and no Cloudflare.

```
browser ──► your-project.vercel.app
              │  /api/*, /auth/google/*  ──►  api      (container: FastAPI, FFmpeg)  ──►  Neon
              │  everything else         ──►  web      (Next.js)
              └─ videos go straight to and from a private Vercel Blob store
                                              runner   (private container: renders and analyses, started on demand)
```

## Choose your Vercel plan first

| | Hobby (free) | Pro ($20 a month per seat, plus usage) |
|---|---|---|
| Commercial use | **Not allowed.** Vercel limits Hobby to personal, non-commercial use, so you can test but not charge customers | Allowed |
| CPU included | 4 CPU-hours a month. See the warning below | Pay as you go |
| Time per request | 300 s | Up to 800 s |
| CPU per request | 1 vCPU | Up to 2 vCPU |
| Longest video at 1080p, 30 fps | About 45 s | About 4 minutes after step 8 (estimated from the 1-core timings) |

**Hobby runs out quickly.** On one core, analysing, checking and rendering a 45-second 1080p video uses about 14 CPU-minutes, so 4 CPU-hours covers about 17 videos a month. Check Vercel's usage page for what happens when you pass the allowance, and don't let customers depend on a Hobby deployment.

Use Hobby to try it. Move to Pro before anyone pays you.

## What you have to do by hand

### 1. Push the code to GitHub
```powershell
git add -A
git commit -m "Deploy on Vercel, rename to Visora"
git push
```

### 2. Create the Vercel project
1. Go to [vercel.com/new](https://vercel.com/new) and import your GitHub repository.
2. Leave **Root Directory** as the repository root. Do not choose `web`.
3. Set **Framework Preset** to **Services**. `vercel.json` defines the rest.
4. Click **Deploy**. Until the settings below are added, this first deployment either fails or shows errors on sign-in, because the API refuses to start without them. It still creates the project, and you need its address.

Note the production address, for example `https://visora.vercel.app`. Steps 5 and 6 use it.

### 3. Connect the database (Neon)
In the project, open **Storage**, click **Create Database**, choose **Neon**, and connect it to the project. This sets `DATABASE_URL`. Use the pooled connection string if you are asked. Tables are created on the first start.

### 4. Connect file storage (Vercel Blob)
In **Storage**, click **Create Database** again and choose **Blob**. **Choose Private access: this can't be changed later**, and public access would expose your customers' videos. Connect it to the project, then check that **Settings → Environment Variables** lists `BLOB_READ_WRITE_TOKEN`. If it isn't there, copy the token from the store's page and add it.

### 5. Create the Google sign-in client
1. Open [Google Cloud Credentials](https://console.cloud.google.com/apis/credentials) and create or choose a project.
2. Configure the **OAuth consent screen**: app name **Visora**, your support email, and these links:
   - Home page: `https://YOUR-ADDRESS`
   - Privacy policy: `https://YOUR-ADDRESS/privacy`
   - Terms: `https://YOUR-ADDRESS/terms`
3. Click **Create credentials → OAuth client ID → Web application**.
4. Under **Authorised redirect URIs**, add exactly `https://YOUR-ADDRESS/auth/google/callback`.
5. Copy the client ID and secret for step 7.
6. **Publish the app** on the consent screen. While it stays in Testing, only the test users you list can sign in.

### 6. Set up magic-link email
The easiest option needs no new account, using a Gmail app password:
1. Turn on 2-Step Verification on the Google account.
2. Create an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Use it as `CC_SMTP_PASSWORD` in step 7, with your Gmail address as `CC_SMTP_USER` and in `CC_MAIL_FROM`.

Gmail allows about 500 emails a day, and mail arrives from your personal address. For real customers, use a provider with your own domain, such as Resend or Postmark. Only the `CC_SMTP_*` values change.

### 7. Add your secrets
1. Copy [`.env.vercel.example`](../.env.vercel.example) to `.env.vercel` and fill it in. Git ignores that file. Leave out `DATABASE_URL` and `BLOB_READ_WRITE_TOKEN` if steps 3 and 4 already set them.
2. Generate the signing key once and keep it. Changing it signs everyone out:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
3. In Vercel, open **Settings → Environment Variables**, click **Import .env**, select **Production**, and upload the file.
4. Open **Deployments**, then **⋯ → Redeploy** on the latest one.

### 8. On Pro only: allow longer videos
1. In **Settings → Functions**, set **Max Duration** to `800` and **CPU** to **Performance** (2 vCPU, 4 GB).
2. Add the four commented-out Pro values from `.env.vercel.example`, then redeploy.

### 9. Check it
```powershell
.\.venv311\Scripts\python qa\vercel_check.py https://YOUR-ADDRESS
```
This tests the site from outside, and any failure says what to fix. Then do these three things yourself, because no script can:
1. **Google:** sign in with a Google account. You should reach onboarding, then the dashboard.
2. **Magic link:** in a private window, sign in with another email address. The email should arrive, its link should work, and you should end up on the dashboard.
3. **A real video:** add a product, upload a short 1080p video, run a decision, then check, approve, render, play and download it.

### 10. Before you charge anyone
- Have a lawyer review `/terms` and `/privacy`. They are a starting point, not legal advice.
- Set up [Vercel spend management](https://vercel.com/docs/spend-management) on Pro, because usage is billed.
- Payments are not built in. Plans are changed by hand with `python -m ops.plans` and billed by invoice.

## What could still go wrong on real Vercel
None of this has run on Vercel, Neon, real Blob, real Google or real email, because they need your accounts. `qa/vercel_rehearsal.py` ran the same containers locally under the same limits, with local stand-ins for those services. These are the things only the real deployment can settle. Vercel lists Services and the Container runtime as **beta**.

| Risk | What you'd see | What to do |
|---|---|---|
| Vercel's build rejects a setting in `vercel.json` | The deployment fails with the reason | Send me the build log |
| Blocked browser uploads to Blob | "Storage refused the upload" | Check the browser console. The upload uses Vercel's own SDK, so this is unlikely |
| Less disk in `/tmp` than a job needs | A render fails with a "no space" error | `/api/health/ready` shows `disk_free_gb`. Lower `CC_MAX_VIDEO_MB` to under a third of it |
| Vercel's CPU is slower than the test machine | "JOB_TIMEOUT" on videos near the limit | Lower `CC_MAX_VIDEO_GIGAPIXELS`, for example to `2` |
| A cold start after 5 idle minutes | The first request is slow | Expected: the container has to start |

## How long jobs take
Measured with the real code in the production image's configuration, on one pinned CPU core and 2 GB, using 1080p and 720p test clips with heavy added noise. Real footage will differ.

| Clip | Upload check | Analysis (per surface examined) | Quality check | Render + validate |
|---|---|---|---|---|
| 60 s, 720p | 2 s | 55 s | 40 s | 111 s |
| 30 s, 1080p | 4 s | 165 s | 113 s | 144 s |
| 60 s, 1080p | 5 s | 170 s | 103 s | 277 s |

- **Analysis** saves its progress after each surface and continues in the next request, so it can take longer than one request allows.
- **A render** must finish in one request. That is why `CC_MAX_VIDEO_GIGAPIXELS` (width × height × frame rate × seconds) sets the limit: 2.9 keeps a 45-second 1080p render near 215 s of the 270 s available, leaving a margin.
- **Jobs run one at a time** by default, so a job never loses its CPU to another one. Each job then waits for those ahead of it, so customers will see queueing at busy times.
- **If the runner dies part way,** the job is retried when the dashboard next polls, or the next time anyone queues work. If nobody has the dashboard open and nothing new is queued, it waits until someone returns. Vercel Cron could close that gap, but on Hobby it runs only once a day.

## Sources
- [Function limits](https://vercel.com/docs/functions/limitations)
- [Hobby plan](https://vercel.com/docs/plans/hobby) and [fair use](https://vercel.com/docs/limits/fair-use-guidelines)
- [Services](https://vercel.com/docs/services) and [Container runtime](https://vercel.com/docs/functions/runtimes/container)
- [Vercel Blob](https://vercel.com/docs/vercel-blob) and [private storage](https://vercel.com/docs/vercel-blob/private-storage)
