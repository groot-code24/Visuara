# V10 release readiness

## Decision: **BLOCKED**
**Recommendation: not ready for launch (pilot or public), yet.**

The application runs locally and in Docker. It is tested on SQLite and PostgreSQL, and verified in browsers on a split-origin (Vercel-style) layout. It held up under a 10-organization concurrent workload with a worker crash, an outage drill and a backup/restore drill. Two mandatory gates are unmet:
1. **Gate D (live infrastructure):** Neon, Cloudflare R2, the SMTP provider, Google OAuth and Vercel have never been connected (no accounts or credentials). Neon's idle behaviour is unverified in its dashboard.
2. **Gate E (visual reliability):** no authorised real creator footage has been evaluated. Every quality and accuracy number is synthetic.

Gate D is configuration and verification work, with scripts ready (checklist below). Once it passes, the platform can become **CONDITIONAL for a supervised pilot**: every render reviewed by a person before delivery (the existing fit review), invoicing done manually, limited to the scene classes the benchmarks cover. Public launch additionally needs gate E and an independent penetration test.

## Gates
| Gate | Status | Evidence / gap |
|---|---|---|
| **A Local development** | **PASS** | Documented setup (Docker stack, or scripts without Docker). Fresh databases per test; 70/70 tests on SQLite and on PostgreSQL 16; browser QA V6 10/10, V8 6/6, V9 6/6 (same origin), V10 10/10 (split origin). Details in [test-evidence.md](test-evidence.md) |
| **B Vercel frontend** | **CONDITIONAL** | Build, routing, headers, `config.js`, no secrets in the output; cross-origin auth, CORS, CSRF and direct upload verified in Chrome through a Vercel-router emulator. No real Vercel deployment |
| **C Backend deployment** | **PASS (local Docker)** | Image built and audited (no known vulnerabilities after the pip upgrade); API and 2 workers in containers; FFmpeg present; jobs processed; SIGKILL crash recovered; graceful hand-back on SIGTERM; liveness/readiness checks; production guard. Not yet on a VM |
| **D Live infrastructure** | **BLOCKED** | Stand-ins only: PostgreSQL 16 (Neon), SeaweedFS S3 gateway (R2), Mailpit (SMTP), stubbed token exchange + JWKS test keys (Google). Backup/restore drilled on PostgreSQL 16, not Neon. Neon idle behaviour inferred from session counts, not the dashboard |
| **E Visual reliability** | **BLOCKED** | 2D and planar results identical to V9. 3D: fresh held-out seeds 8–10 36/36, 0 false accepts, 0 false rejects; the tolerance defect is fixed but exposed a pose-quality gap. **Real-footage validation not performed** |
| **F Security** | **CONDITIONAL** | Role and tenant tests, 720 cross-tenant probes with no leaks, consent and licence chain, signed links, header and CORS controls, clean dependency audit, secret scan 0 findings. No independent pen test; residual risks in [security-review.md](security-review.md) |
| **G Ten organizations** | **PASS (local stack)** | 15 organizations / 40 users concurrently; invitations and roles; quotas tested; queue measured; crash recovery; 0 leaks. Not measured on Neon/R2 latency or for long durations |
| **H Launch decision** | **BLOCKED** | D and E unmet |

## Production deployment checklist (gate D)
1. **Domain:** `app.example.com` (Vercel) and `api.example.com` (VM) on one registrable domain.
2. **Neon:** create the project, put the pooled URL in `deploy/.env`, run `python -m ops.migrate` → `engine=postgres neon=True`.
3. **R2:** private bucket and object-scoped token in `deploy/.env`; `python -m ops.storage_setup --app-origin https://app.example.com --apply` must print all checks `true`.
4. **SMTP:** provider account; SPF, DKIM and DMARC on the sending domain; send codes to Gmail and Outlook test inboxes and check that all three show PASS.
5. **Google:** OAuth client (Web application) with redirect URI `https://api.example.com/auth/google/callback`; consent screen published; complete a real sign-in (new user → onboarding; existing email → linked identity).
6. **VM:** `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env --profile caddy up -d --build`; `/api/health/ready` must be 200 with database and object storage `ok`.
7. **Vercel:** set `CC_API_ORIGIN`; deploy `main`; run the checks in [vercel-deployment.md](vercel-deployment.md#verify-after-the-first-deployment).
8. **End to end on production:** run [scenarios A–M](test-evidence.md#end-to-end-scenarios) by hand with two test workspaces, including a direct upload to R2 and a render download.
9. **Backups:** logical backup → restore into an empty Neon branch → compare counts; also a Neon point-in-time branch restore.
10. **Neon idle:** leave the system idle for 30 minutes and check Neon → Monitoring shows suspended compute between worker polls.
11. **Monitoring:** an external uptime check on `/api/health/live`; `ops.monitor` on a schedule with the alert webhook; fill in the incident contacts.
12. **Git and CI:** create the private GitHub repository, push, confirm the `ci` workflow is green, protect `main`, add the deploy secrets.

## Next three actions
1. **Connect the live services and run the checklist above** (gate D). It needs your accounts; about a day of configuration with the scripts provided.
2. **Run the real-footage benchmark** (`benchmark/real_footage.py`): 50–100 consented clips from 10 or more creators, a creator-level split and 3 or more blind raters. Then recalibrate the gates on dev data only and report test results with confidence intervals (gate E).
3. **Close the 3D pose-quality gap and commission a pen test:** a per-frame pose-error gate calibrated on new dev seeds and validated on fresh seeds; an independent penetration test of the deployed platform.
