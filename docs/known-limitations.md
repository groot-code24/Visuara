# Known limitations

## Not verified on the real services
- **Vercel:** the app has never run on real Vercel or real Blob. `qa/vercel_rehearsal.py` runs the same containers under the same limits with local stand-ins. Vercel lists Services and the Container runtime as beta.
- **Sign-in:** the Neon connection and the Google client ID and secret have been accepted by the real services, but a real Google sign-in and a real magic-link email have not been completed. [vercel.md](vercel.md) lists the final checks.
- **Footage and security review:** no real creator footage has been evaluated, and there has been no independent penetration test.
- **CI:** the GitHub Actions workflow has not been confirmed to run.

## Vercel's free plan
- **Not for business use:** Vercel limits Hobby to personal, non-commercial use, so move to Pro before charging customers.
- **Video length:** a render must finish in one request. That is about 45 seconds of 1080p at 30 fps, or about 100 seconds of 720p. The longer limits advertised on paid plans need Pro and the settings in [vercel.md](vercel.md), and are estimated from one-core timings.
- **Monthly allowances:** 4 CPU-hours covers about 17 of the longest videos. Blob includes 1 GB of storage, 10 GB of transfer and 2,000 advanced operations. A job can download a full-size video from Blob several times, so transfer also runs out at about 15 of the largest videos. These two estimates assume every video is at the limit.
- **Blob lockout:** past its allowance, Vercel blocks a Hobby project's Blob store for up to 30 days. `CC_PLATFORM_STORAGE_MB=700` stops new uploads and renders before storage reaches that point. Nothing caps transfer or operations, so watch them on Vercel's usage page.
- **Storage can only fill up:** products and videos can't be deleted from the web app, so when the 700 MB ceiling is reached, uploads and renders stop until you delete media by hand or move to Pro and raise the ceiling.
- **Queueing:** jobs run one at a time by default, so customers wait behind each other at busy times.
- **Recovery:** a job whose runner died is restarted by dashboard polling or the next queued job. With nobody on the dashboard, it waits.
- **Cleanup:** expired sessions and abandoned uploads are removed when a job runs or someone signs in, at most every 6 hours. With no activity at all, they wait. Expired sessions and codes are rejected either way.
- **Disk:** the size of `/tmp` on the container runtime isn't documented. `/api/health/ready` reports it once deployed.
- **Render files are larger:** the faster encoder preset roughly doubles output size at the same quality setting.

## Decision engine
- **Placements:** flat surfaces only (posters, signs, screens, packaging faces, tabletops), one product per video per decision. The 3D path needs a `.glb` model and is reachable through the API only.
- **Surface detection:** contour-based. It misses soft-edged, curved or cluttered surfaces, and real footage will produce fewer candidates than the synthetic test clips.
- **Scoring:** hand-set heuristics, not learned or calibrated.
- **Quality checks run on the API:** at most two at a time. A request answers "still measuring" before the time limit and the page keeps polling. A restart loses a running check, and running it again is safe.

## Product
- **Payments:** billing is by invoice. Plan changes are an operator command (`python -m ops.plans`), and there is no checkout.
- **Legal pages:** `/terms` and `/privacy` match what the software does, but no lawyer has reviewed them, and they name a legal entity and governing law only once `NEXT_PUBLIC_LEGAL_NAME` and `NEXT_PUBLIC_LEGAL_JURISDICTION` are set.
- **One workspace per email address.**

## Security
- **Inline scripts:** the content security policy allows inline scripts, because Next.js static pages need them unless every page is rendered per request with a nonce. Storage, upload and connection destinations are restricted to Vercel Blob.
- **Git history:** a Neon password was committed in `c37197a`. Changing the password makes that copy useless, and removing it from history needs a force push.

## Platform
- Rate limits live in the database. For well beyond 10 organisations, use Redis or a gateway.
- A database that freezes without dropping connections holds requests until the platform's time limit.
