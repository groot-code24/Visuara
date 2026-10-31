# Known limitations (V11)

## Not verified (no accounts or credentials were available)
- **Live services:** Neon, Cloudflare R2, a real SMTP provider, a real Google sign-in and a Vercel deployment have never been connected. Every integration is implemented and was exercised against local stand-ins:
  - PostgreSQL 16 for Neon;
  - the SeaweedFS S3 gateway for R2;
  - Mailpit for SMTP;
  - a placeholder OAuth client for the redirect, plus stubbed token exchange and local RSA keys in tests for Google;
  - `next start` for Vercel.
- **Vercel rewrite behaviour:** forwarding of cookies and `X-Forwarded-For`, body size and duration limits. See [deployment.md](deployment.md#to-verify-on-the-first-real-deployment-never-done).
- **Footage and security review:** no real creator footage has been evaluated, and there has been no independent penetration test.
- **CI:** GitHub Actions has never run (no remote).

## Decision engine
- **Placements:**
  - flat surfaces only (posters, signs, screens, packaging faces, tabletops), placed as a flat graphic;
  - one product per video per decision;
  - a product photo alone cannot be stood up as a 3D object. The 3D path needs a `.glb` model, and in V11 it is reachable through the API only, not the web app.
- **Surface detection:** contour-based. It misses soft-edged, curved or cluttered surfaces, and real footage will produce fewer candidates than the synthetic room clips.
- **Scoring:** hand-set heuristics. Weights and category affinities are not learned or calibrated. Without a local vision model, "scene context" is the surface type only.
- **Runtime:**
  - about 30 s per short 640×360 clip on the test machine;
  - about 110 s per clip in Docker during the 10-workspace load test, with 2 workers sharing the VM with the API;
  - 4K videos have not been measured.
- **Long videos are slow (measured on a 5-minute 1080p synthetic clip, Docker, 8 CPUs shared):**
  - decision: about 3.5 minutes for that one video, so a batch of 20 takes over an hour on one worker;
  - quality check: 52 s for a 5-second placement, so about 10 s per second of placement, and proportionally longer for long placements;
  - the numbers will differ on real footage and on a production VM.
- **Quality checks run on the API host, not the workers:**
  - at most two run at a time (`CC_QUALITY_CONCURRENCY`), and the rest wait. A request answers "still measuring" after 90 s and the page keeps polling, so Vercel's 120 s limit is not hit.
  - each uses several CPU cores and slows other API requests, so give the API at least 2 CPUs. Moving checks to the workers is the next step if this becomes a bottleneck.
  - a restart of the API loses a running check; running it again is safe.

## Product
- **Payments:** billing is by invoice. Plan changes are an operator command, and there is no checkout, so nobody can buy a paid plan by themselves. "Start with Pro" creates a Free workspace and asks them to contact you. Usage meters and limits are enforced.
- **Legal pages:** `/privacy` and `/terms` describe what the software does, but they have not been reviewed by a lawyer, and they name no legal entity.
- **Removed for the MVP:** the creator marketplace, licensing between workspaces, and click and conversion attribution (see [changes.md](changes.md)).
- **No deletion in the UI:** products and videos cannot yet be deleted from the web app, so a workspace cannot free its own storage. Deletion on request is an operator procedure.
- **One workspace per email address.**

## Platform (from V10, still true)
- Rate limits live in the database; for well beyond 10 organisations, use Redis or a gateway.
- The worker wake-up file needs the API and workers on one host or volume.
- A database that freezes without dropping connections holds requests until the proxy times out.
- Direct uploads are a single PUT (up to 5 GB, no resume).
