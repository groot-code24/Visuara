# Deploy Visuara on Vercel

Everything runs in one Vercel project on the free plan: the website, the API and the video jobs. The only other accounts are Neon for the database, Google for sign-in, and an email account for magic links. Each has a free option.

```
browser ──► your-project.vercel.app
              │  /api/*, /auth/google/*  ──►  api      (container: FastAPI, FFmpeg)  ──►  Neon
              │  everything else         ──►  web      (Next.js)
              └─ videos go straight to and from a private Vercel Blob store
                                              runner   (private container: renders and analyses, started on demand)
```

## 1. Finish `.env.vercel`
Your secrets stay in `.env.vercel`, which git ignores. **Never put real values in any other file. Vercel never reads this file by itself:** step 5 is what gives Vercel your settings.

| Setting | Where it comes from | Needed for |
|---|---|---|
| `DATABASE_URL` | Neon's **pooled** connection string | Everything |
| `BLOB_READ_WRITE_TOKEN` | Set for you in step 3, so delete this line from the file | Video storage |
| `CC_SIGNING_KEY` | Run `python -c "import secrets; print(secrets.token_urlsafe(48))"` once and keep it. Changing it signs everyone out | Everything |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Step 4 | Google sign-in |
| `CC_SMTP_HOST`, `CC_SMTP_PORT` | `smtp.gmail.com` and `587` for Gmail | Magic links |
| `CC_SMTP_USER` | Your full Gmail address | Magic links |
| `CC_SMTP_PASSWORD` | A Gmail **app password**, not your normal password | Magic links |
| `CC_MAIL_FROM` | `Visuara <your-address@gmail.com>` | Magic links |
| `NEXT_PUBLIC_CONTACT_EMAIL` | The address customers write to. The legal pages show it | Legal pages, upgrades |

**To create the Gmail app password:** turn on 2-Step Verification on the Google account, then create a password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Gmail allows about 500 emails a day and shows your personal address, so move to a provider with your own domain, such as Resend or Postmark, for real customers. Only the `CC_SMTP_*` values change.

**To check the file** without showing any values, including whether a test email arrives:
```powershell
.\.venv311\Scripts\python qa\check_env.py --mail your-address@gmail.com
```
It reports the Blob token as missing until step 3. That is expected.

With a custom domain, also add `CC_PUBLIC_URL` and `NEXT_PUBLIC_SITE_URL`, set to the exact address the site ends up on after any redirect, such as `https://www.your-domain.com`.

Magic links and uploads are optional when the API starts: without them, Google sign-in still works, and the API logs what is off. At least one sign-in method must work, and security settings must be right, or the API refuses to start and Vercel's **Logs** says why.

Optional settings:
- `NEXT_PUBLIC_LEGAL_NAME` and `NEXT_PUBLIC_LEGAL_JURISDICTION`: your registered business name and governing law, for example `India`. The legal pages use them.

## 2. Push the code and create the project
1. Check that `git config user.email` is the email on your GitHub and Vercel accounts. On the free plan, Vercel blocks deployments of a private repository's commits unless their author owns the Vercel account.
2. Run:
   ```powershell
   git add -A
   git commit -m "Rename to Visuara, new type and legal pages, free plan limits"
   git push
   ```
3. Go to [vercel.com/new](https://vercel.com/new) and import your GitHub repository.
4. Leave **Root Directory** as the repository root. Do not choose `web`.
5. Set **Framework Preset** to **Services**. `vercel.json` defines the rest.
6. Click **Deploy**. It stops with an error until the settings are added. It still creates the project, and it gives you the address you need next.

## 3. Create the video store
In the project, open **Storage → Create → Blob**. **Choose Private: this can't be changed later**, and public access would expose your customers' videos. Connect it to the project, then check that **Settings → Environment Variables** lists `BLOB_READ_WRITE_TOKEN`.

## 4. Point Google at your address
In [Google Cloud Credentials](https://console.cloud.google.com/apis/credentials), for your OAuth client:
1. Under **Authorised redirect URIs**, add exactly `https://YOUR-ADDRESS/auth/google/callback`.
2. On the **consent screen**, set the app name to **Visuara**, and add `https://YOUR-ADDRESS`, `https://YOUR-ADDRESS/privacy` and `https://YOUR-ADDRESS/terms`.
3. **Publish the app.** While it stays in Testing, only the test users you list can sign in.

## 5. Import your secrets and redeploy
1. Open **Settings → Environment Variables → Import .env**, select **Production**, and upload `.env.vercel`. If a name already exists, such as `BLOB_READ_WRITE_TOKEN` from step 3, replace it or leave that one out.
2. Check the page lists `DATABASE_URL`, `CC_SIGNING_KEY` and `GOOGLE_CLIENT_ID` for Production.
3. Open **Deployments**, then **⋯ → Redeploy** on the latest one. Changes never reach a deployment that already exists.

## 6. Check it
```powershell
.\.venv311\Scripts\python qa\vercel_check.py https://YOUR-ADDRESS
```
This tests the site from outside, and any failure says what to fix. Then do these yourself, because no script can:
1. **Google:** sign in with a Google account. You should reach onboarding, then the dashboard.
2. **Magic link:** in a private window, sign in with another email address. The email should arrive, its link should work, and you should reach the dashboard.
3. **A real video:** add a product, upload a short 1080p video, run a decision, then check, approve, render, play and download it.

## What the free plan gives you
| Allowance per month | Amount | What that means here |
|---|---|---|
| CPU | 4 hours | About 17 videos at the maximum length |
| Blob storage | 1 GB | Visuara stops new uploads and renders at 700 MB of media |
| Blob transfer | 10 GB | About 15 videos at the maximum size, because a job can download the source several times |
| Blob advanced operations | 2,000 | Each upload and saved file uses some |
| Longest video | | About 45 s at 1080p, or 100 s at 720p |

The transfer and CPU figures assume every video is at the limit, so ordinary use goes further. Check Vercel's usage page often. **If Blob goes past its allowance, Vercel blocks it for up to 30 days**, and the storage ceiling above is there to prevent that. Products and videos can't be deleted from the web app yet, so reaching the ceiling means deleting media by hand or moving to Pro.

Vercel limits the free plan to personal, non-commercial use. It's right for building and showing the product. **Move to Pro ($20 a month per seat, plus usage) before anyone pays you**, then:
1. In **Settings → Functions**, set **Max Duration** to `800` and **CPU** to **Performance**.
2. Add these settings and redeploy. The video limits are estimated from one-core timings:
   ```
   CC_FUNCTION_SECONDS=800
   CC_MAX_VIDEO_GIGAPIXELS=15
   CC_MAX_VIDEO_MB=1024
   CC_RUNNER_CONCURRENCY=2
   CC_PLATFORM_STORAGE_MB=0
   ```
3. Set up [spend management](https://vercel.com/docs/spend-management), because usage is billed.

## If a secret leaks
1. Change it at the provider first (Neon, Google or Gmail). This is what actually protects you.
2. Update `.env.vercel` and Vercel's environment variables, then redeploy.

Removing the old value from git history doesn't help on its own, because anyone could already have copied it. The commit hook in `.githooks/pre-commit` exists to stop this happening again.

## If sign-in says it can't reach the service
Open **Logs** and filter to `/api/auth/providers`. A line saying `Refusing to start` lists exactly what's wrong. If it says `DATABASE_URL is not set`, Vercel doesn't have your settings: repeat step 5.

## What could still go wrong on real Vercel
`qa/vercel_rehearsal.py` ran the same containers locally under the same limits, with local stand-ins for Blob and email. Only a real deployment can settle these. Vercel lists Services and the Container runtime as **beta**.

| Risk | What you'd see | What to do |
|---|---|---|
| Vercel's build rejects a setting in `vercel.json` | The deployment fails with the reason | Send me the build log |
| Blocked browser uploads to Blob | "Storage refused the upload" | Check the browser console. A content-security-policy error means `web/next.config.ts` needs the blocked address added |
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
- **A render** must finish in one request. That is why `CC_MAX_VIDEO_GIGAPIXELS` (width × height × frame rate × seconds) sets the limit: 2.9 keeps a 45-second 1080p render near 215 s of the 270 s available.
- **Jobs run one at a time** by default, so a job never loses its CPU to another one. Customers will see queueing at busy times.
- **If the runner dies part way,** the job is retried when the dashboard next polls, or the next time anyone queues work.

## Sources
- [Function limits](https://vercel.com/docs/functions/limitations), [Hobby plan](https://vercel.com/docs/plans/hobby) and [fair use](https://vercel.com/docs/limits/fair-use-guidelines)
- [Services](https://vercel.com/docs/services) and [Container runtime](https://vercel.com/docs/functions/runtimes/container)
- [Vercel Blob pricing and limits](https://vercel.com/docs/vercel-blob/usage-and-pricing) and [private storage](https://vercel.com/docs/vercel-blob/private-storage)
- [Git author rules on Hobby](https://vercel.com/docs/deployments/troubleshoot-project-collaboration)
