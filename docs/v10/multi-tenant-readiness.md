# Multi-tenant readiness: first 10 organizations

## Model
- **Organizations** are independent workspaces (brand or creator; shared workspaces may hold both kinds of account).
- **Members** belong to one workspace, with a member role:

  | Role | Can |
  |---|---|
  | owner | everything, including ownership transfer |
  | admin | team management (invite, change roles, suspend, remove; not admins unless owner) plus everything a manager can do |
  | manager | campaigns, offers, licences, approvals, consent, renders, downloads |
  | editor | uploads, products, scene analysis, placements, quality checks, revisions |
  | viewer | read-only reviewer |

  "Creator" is the account type of creator workspaces; the same member roles apply inside them.
- **Enforcement:** one choke point, `current_user()` → `members.authorize()`, maps every method and path to a minimum role. GET needs viewer; other methods need manager unless a rule lowers or raises it. Organization scoping is applied in every query (`owned()`, licence grants), and cross-organization access exists only through an accepted offer's grant.
- **Invitations:** admin+; by email (sent when SMTP is configured); single use; 7-day expiry; revocable; listed with status. Accepted by signing in with the invited address (Google, email code or magic link) or by password registration with the token.
- **Suspension and removal:** sessions end immediately. Removal frees the email (the record is pseudonymised and kept for the audit trail). Owners cannot be suspended or removed, only replaced through ownership transfer to an active admin.
- **Audit:** `member.*`, `invitation.*`, `auth.*`, `upload.*`, `payment.*` and all workflow events, per organization.

## Operating limits (defaults; environment variables)
| Limit | Default | Variable |
|---|---|---|
| Storage per organization | 10 GB | `CC_ORG_STORAGE_QUOTA_MB` |
| Video uploads per organization per day | 25 | `CC_ORG_DAILY_VIDEO_UPLOADS` |
| Queued + running renders per organization | 2 | `CC_ORG_MAX_ACTIVE_JOBS` |
| Scene analyses per organization per hour | 30 | `CC_ORG_ANALYSES_PER_HOUR` |
| Quality checks per organization per hour | 40 | `CC_ORG_QUALITY_CHECKS_PER_HOUR` |
| Members per organization | 25 | `CC_ORG_MAX_MEMBERS` |
| Invitations per organization per hour | 30 | (fixed) |
| Direct-upload starts per organization per hour | 60 | (fixed) |
| Sign-in attempts per IP per minute | 12 | `CC_AUTH_RATE_PER_MINUTE` |
| Video size / duration / resolution / fps | 500 MB / 600 s / 3840×2160 / 120 | `CC_MAX_VIDEO_*` |
| Render wall-clock time | 30 min | `CC_JOB_MAX_SECONDS` |

Fair claiming serves organizations with fewer running jobs first, so one organization's backlog cannot hold every worker. Each organization sees its own usage and limits under **Settings → Usage and limits** (`GET /api/usage`).

## Concurrency test
`qa/load_10_orgs.py` against the Docker stack: PostgreSQL 16, the S3 gateway, the API container, and two worker containers on one Windows 11 machine (Docker Desktop VM: 8 CPUs, 3.5 GB RAM). All traffic goes through the public HTTP API.

| | |
|---|---|
| Organizations | **10 brand + 5 creator** (15) |
| Users | **40**: brand owner, manager and editor; creator owner and manager. Teammates joined by invitation |
| Concurrency | 10 parallel clients |
| Workload | Sign-ups, invitations and concurrent sign-ins; 10 videos uploaded, analysed and listed; 10 products, 10 campaigns, marketplace searches, 10 offers accepted; 10 placements quality-checked, consented and approved; **10 renders queued at once**; download links and downloads; an editor refused a download |
| Crash drill | One of the two workers was killed with SIGKILL while 2 renders were running |
| Isolation probes | Every brand manager tried every other brand's product image, placement, preview, campaign report, render download, download link and payment ledger, plus every unlicensed creator source video |

Results (final code): **1,535 requests with 0 unexpected responses; 10/10 renders succeeded (one recovered after the worker crash); 10/10 downloads; 720/720 isolation probes refused, 0 leaks; at most 12 PostgreSQL connections.** Full tables in [test-evidence.md](test-evidence.md#ten-organization-concurrency). Raw report: `docs/v10/evidence/load_10_orgs.json`.

## What this does and does not establish
- **Established on the local stack:**
  - 15 organizations and 40 users work concurrently with no unexpected responses;
  - no cross-tenant access in 720 probes;
  - role restrictions hold under load;
  - every render completes exactly once, including after a hard worker crash;
  - PostgreSQL connections stay bounded (about 12).
- **Not established:**
  - behaviour on Neon (network latency per query), on R2, or on the production VM size;
  - long-duration operation (hours or days);
  - larger media (the test clips are short synthetic videos);
  - real user behaviour.

  The synchronous quality check (about 13 s under this load) and analysis (about 14 s) dominate API latency when several organizations run them at once (see technical-debt.md).
- **Capacity estimate (inference, not a measurement):** two workers rendered 10 short synthetic jobs at a median 2.5 s each (max 7 s). For the pilot, the limiting factor is render CPU per minute of video, not organization count. Start with 1–2 workers on 4 vCPU and scale on `oldest_queued_age_seconds`.
