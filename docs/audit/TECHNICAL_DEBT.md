# Technical debt

Ranked by how much it blocks the V2.0 specification. Severity: **High** blocks a release gate, **Medium** costs time or safety, **Low** is cosmetic. Updated 5 November 2026 after Phases 1 to 15, the final audit and the deployment hardening; resolved rows keep their number and say what resolved them.

## Open
| # | Debt | Where | Severity | Why it matters | Plan |
|---|---|---|---|---|---|
| 16 | **Scene cuts come from colour histograms.** A cut less than `min_scene_seconds` (1.0 s) before the end is merged into the last scene | `intelligence.segment` | Medium | Very short closing shots are not separate scenes | Deliberate (a sub-second tail cannot hold a placement). Brightness drift no longer reads as a cut (`cut_gain_normalise`, 4 Nov 2026). Real-footage evaluation needs B-7 |
| 14 | Unverified hosting features | Vercel Services and Container runtime are beta | High | The live API has never started successfully | B-2; and `qa/vercel_rehearsal.py` has not run on the Phase 2–15 code because Docker does not respond on this machine |
| 17 | No payment provider; payment is recorded by a platform administrator | `app/commercial.py` | Medium | Spec §7.10 asks for payment capture | B-4. The ledger, invoices and idempotency are in place for a provider adapter |
| 19 | The legacy `frames` table is still refilled after each analysis | `intelligence._complete` | Low | Two representations of the same scenes | Kept so the decision engine and older views keep working; remove when they read `scene_frames` |
| 20 | `POST /api/videos/{vid}/analyze` (legacy keyframes) still runs inside the request | `app/main.py` | Low | The staged job is the supported path | Retire once the web app no longer calls it |
| 23 | Uploading a new version of a video has no web control | `POST /api/videos/{vid}/versions` | Low | Versions work through the API and are tested; the Library offers deletion but not replacement | Add an upload-new-version action to the Library |
| 18 | Outbox events for consumers other than reports wait for the next runner request in HTTP mode | `app/outbox.py`, `app/jobrunner.py` | Low | Campaign reports deliver pending events before they are read, so the only consumer today is never stale | Revisit when a consumer has an effect outside reads |
| 22 | On PostgreSQL every pending migration runs in one transaction | `migrations.apply` | Low | A long backfill on a large database holds the advisory lock for its whole length | Current backfills are small; split a large one into its own release |
| 10 | Mixed error shapes | various | Low | Some errors are strings, most are `{code, message}` | Normalise as routes are touched; all Phase 2–15 routes use `{code, message}` |
| 12 | Rate limits in the database | `rate_limits` | Low | Fine at MVP scale | Revisit with load measurements |
| 13 | Plan prices are whole dollars | `app/limits.py` | Low | Marketplace money is integer minor units with a currency (Phase 11); plan prices are display values only | Convert when plans are charged through a provider |

## Resolved
| # | Debt | Resolved by |
|---|---|---|
| 1 | A user belongs to exactly one organization | Phase 2: `memberships`, `sessions.active_org_id`, switching, invitations for existing accounts. `users.org_id` remains the default organization |
| 2 | Re-analysis deleted earlier results | Phases 1 and 4: immutable `analysis_versions` with their own scenes and frames; a new analysis never touches an earlier version |
| 3 | Keyframe analysis ran inside the HTTP request | Phase 4: a staged, leased, resumable background job (row 20 covers the legacy endpoint that remains) |
| 4 | Opportunities existed only inside `decisions.result_json` | Phase 5: typed `opportunities` rows, validated, with score breakdowns and inventory |
| 6 | `app/main.py` held most of the domain | Phases 2–13 added 14 router modules; `main.py` keeps the pre-existing routes |
| 7 | No migration history | Phase 1: `app/migrations.py` with checksums in `schema_migrations` |
| 8 | No content hash on videos; no deletion | Phase 3: SHA-256 per version, background hashing of older videos, duplicate refusal, soft deletion with storage clean-up |
| 9 | Scoring weights were constants | Phase 5: versioned `scoring_configs`, stored with every opportunity |
| 11 | CSP allowed inline scripts | 5 Nov 2026: a per-request nonce with `'strict-dynamic'` (`web/proxy.ts`, `web/lib/csp.ts`); every page renders per request; checked by the marketplace browser walk |
| 5 | No object detection model | 5 Nov 2026 (V4.0 M14): YOLOX-Tiny, benchmarked against four other Apache-2.0 models; detection and tracking stages run (`app/detection.py`, `app/objtrack.py`) |
| 15 | The surface detector found plain surfaces only (3 of 42 textured posters) | 5 Nov 2026 (V4.0 M14): straight-edge quadrilaterals (`app/surfaces.py`) find 30 of 42; each surface is followed by homography. The decision engine (`app/decisions.py`) still uses the contour detector alone |
| 24 | An opportunity's explanation kept the brand-safety verdict it was scored with after a person's review | 6 Nov 2026 (V4.0 M15): the review appends "Reviewed: <verdict> by a person." (replacing an earlier one) |
| 21 | No ESLint in `web/` | 5 Nov 2026 (V4.0 M13): `eslint.config.mjs` with the Next.js 16 rules; `npm run lint` in CI; 0 errors. Its first run found a stale-response bug in `useApi` (D-FE-01) |
| 3a | No web control to delete a video | 5 Nov 2026: Library offers Delete to managers, with a confirmation; a video in use is refused with the reason (browser walk) |
