# Release report (V4.0, as of the end of M17)

**Date:** 6 November 2026. **Code:** the working tree on `3bf569f`, uncommitted. The owner commits and pushes; nothing here was committed, pushed or deployed.

## Release status: **NOT READY FOR RELEASE**
- **The production database password is public** (D-SEC-00, P0) until the owner resets it.
- **The live deployment has never served traffic** (B-2), and live Google sign-in is rejected (B-3).
- **CI has never run on this code**, because it runs only when the owner pushes.
- **Accuracy on real video has never been measured** (B-7). Detection, tracking and segmentation are real and evaluated, but on real photographs and synthetic video only.
- **Payments through Stripe have not been verified against Stripe** (B-4). The provider flow works end to end in test mode with the built-in sandbox.

The code itself passes everything that can run on this machine (§11). **What the owner does next, in order: [PRE_PRODUCTION_CHECKLIST.md](PRE_PRODUCTION_CHECKLIST.md).** After its steps 1–6 the product is fit for a controlled beta.

## 1. Executive summary
**M13, release stabilization,** is done as far as the code can take it:
- seven security and reliability defects fixed;
- a sweep showing no organization can reach another's records;
- ESLint and frontend tests in CI;
- the first deployment rehearsal on the current code.

**M14, intelligence,** replaces the stages that used to say `UNAVAILABLE` with real, evaluated methods:
- **Object detection:** YOLOX-Tiny, Apache-2.0, chosen by benchmark from five licensed CPU models.
- **Object tracking:** stable identities, occlusion gaps that are never filled in, and recovery by appearance.
- **Surfaces:** textured surfaces found in 30 of 42 clips, up from 3, and each followed through its scene by homography.
- **Segmentation:** placed on the exact frame, flashes ignored, dissolves and fades typed.
- **Scene understanding** from measurements, and a validated vision-model interface.
- **Scoring v2 and matching v2,** with evidence for every factor.

On the way, M14 found a P1 rights defect: inventory whose rights lapsed after listing could still be sold (D-MKT-01). It's fixed and tested.

**M15, marketplace completion:**
- **Payments through a provider:** one interface, a test-mode sandbox and a Stripe adapter. Signed webhooks are applied exactly once and only when amount, currency and invoice match; they post the same ledger transaction as a bank transfer. Live money needs the owner's explicit `CC_PAYMENT_LIVE=1`.
- **Agency delegation:** a brand gives an agency access; the agency's people act within the grant, never above manager, and lose access the moment it's revoked.
- **Video versions in the library,** uploaded straight to storage like first uploads (D-MKT-02).
- **The browser journey now includes paying through the test checkout.**

**M16, hardening:**
- **A load test,** which found the campaign list making four queries per campaign (D-PERF-01, 2.2 s at the median). Fixed.
- **Monitoring** of analyses, payments that need a person, and the ledger, with alerts.
- **A container vulnerability scan in CI** (D-QA-06), with Debian's security updates applied at build.
- **Runbook sections** for payments, agencies and monitoring, and the owner's pre-production checklist.

**M17, programmatic foundation:**
- **Versioned contracts** for the inventory feed, deals and delivery reports.
- **Adapter interfaces and capability discovery** with the four states.
- **Test adapters,** always reported as not external, and refused in production.
- **Contract verification** by an administrator.
- No partner is connected, and nothing says one is.

## 2. Initial repository state
`main` at `3bf569f`, with 25 modified and 46 untracked files (the Phase 1–15 work). Detail: [BASELINE.md](BASELINE.md).

## 3. Architecture verified
- **Services:** web (Next.js 16), and the API and job runner (one FastAPI image, two Vercel services).
- **Storage and media:** PostgreSQL or SQLite, Blob or S3, FFmpeg and OpenCV.
- **Detection:** runs in the runner on OpenCV's own ONNX engine, so there's no new runtime dependency. The model is fetched and checksum-verified at image build.
- **Maps:** [DEPENDENCY_MAP.md](DEPENDENCY_MAP.md) has the import graph, with M14's modules, and the source of truth for every entity.

## 4–5. Defects found, root causes and fixes
Thirty-two defects are recorded with reproduction, root cause and verification in [DEFECT_REGISTER.md](DEFECT_REGISTER.md).

| Status | IDs |
|---|---|
| Fixed and tested | D-SEC-01 to 06; D-FE-01 to 03; D-QA-01 to 05, 07, 08; D-OPS-01; D-MKT-01 to 03; D-AI-01 to 07; D-PERF-01 |
| Open, needing an outside step | D-SEC-00 (owner), D-SEC-07 (a Vercel preview), D-QA-06 (the scan runs in CI on the owner's push) |
| Accepted | D-SEC-08 |

## 6. Files and migrations changed
- **M15 to M17 migrations,** all additive:
  - `0017_payments_agencies`: payment attempts, provider events, agency grants;
  - `0018_version_uploads`: an upload session can name a video;
  - `0019_integration_checks`: programmatic verification results.
- **M15 to M17 code:**
  - new: `app/payments.py`, `app/agencies.py`, `qa/load_test.py`, `benchmark/real/ANNOTATION_GUIDE.md`;
  - rewritten: `app/programmatic.py`;
  - changed: `app/commercial.py` (manual payments use `payments.record_payment`), `app/main.py` and `app/orgs.py` (delegated organizations), `app/videos.py` and `app/uploads.py` (direct version uploads), `app/campaigns.py` (batched list), `app/inventory.py` (review note), `app/obs.py` and `ops/monitor.py` (metrics and alerts), `app/config.py` (payment and test-adapter checks), `qa/vercel_check.py` (model check), `qa/browser_qa_marketplace.py` (test-mode payment);
  - web: the finance page with the test checkout page, organizations (agency access), library (versions);
  - CI: container scan, model check; `Dockerfile.vercel`: security updates;
  - tests: new `test_payments.py`, `test_agencies.py`, `test_programmatic.py`, plus additions to `test_blob.py`, `test_marketplace.py`, `test_platform.py`.
- **Migrations:** `0016_intelligence_v2` (M14) adds:
  - appearance, track confidence, observed fraction and gaps to the existing `detected_objects` and `object_tracks` tables;
  - their indexes;
  - scoring configuration v2.
  It is additive: no data changes, and v1 stays.
- **M14 backend:**
  - new: `app/detection.py`, `app/objtrack.py`, `app/surfaces.py`, `app/understanding.py`, `app/vision.py`;
  - changed: `app/intelligence.py` (stages, segmentation, objects endpoint), `app/scoring.py` (v2), `app/catalogue.py` (matching v2), `app/inventory.py` and `app/campaigns.py` (sale-time rights checks), `app/opportunities.py`, `app/main.py` (the vision call through the interface; detection in health), `app/migrations.py`.
- **M14 tooling:**
  - new: `ops/fetch_models.py`, `benchmark/coco_eval.py`, `evaluate_detection.py`, `evaluate_tracking.py`, `evaluate_segmentation.py`, `fixtures_cuts.py`;
  - extended: `evaluate_intelligence.py`;
  - also: `models/README.md` and the upstream licences.
- **Build and CI:** `Dockerfile.vercel` fetches the model; CI fetches it, caches it and requires it (`CC_REQUIRE_MODELS=1`).
- **Frontend:** `web/app/dashboard/analyses/[id]/page.tsx`.
- **Tests:**
  - new: `test_detection.py`, `test_tracking_objects.py`, `test_surfaces.py`, `test_understanding.py`;
  - extended: `test_intelligence.py`, `test_marketplace.py`.
- **M13 changes:** listed in the M13 report section of [REGRESSION_LOG.md](REGRESSION_LOG.md).
- **API compatibility:** additions only:
  - `GET /api/analyses/{id}/objects`;
  - a decoded `score_breakdown` in the opportunity list;
  - `reasons` and `left_out` in recommendations;
  - `object_detection` in health.
  Two behaviour changes, both intended:
  - a sale step on lapsed inventory answers 409 `NOT_SELLABLE`;
  - the analysis's detection and tracking stages run instead of reporting `UNAVAILABLE`.

## 7. Implemented features
M14 status by item, with evidence: [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md). Earlier features: [docs/release/MVP_RELEASE_REPORT.md](../release/MVP_RELEASE_REPORT.md).

## 8. Security findings and remediation
[SECURITY_REVIEW.md](SECURITY_REVIEW.md), including §6 for M14:
- the sale-time rights checks;
- model weights pinned by checksum;
- vision calls limited and going only to a configured address;
- the new endpoint covered by the cross-tenant sweep (84 identifier routes).

## 9. AI models and licensing
| Use | Model | Licence | Status |
|---|---|---|---|
| Object detection | YOLOX-Tiny, release 0.1.1rc0 (`yolox-tiny@427cc366d34e`) | Apache-2.0; licence in `models/licenses/` | In use; evaluated on real photographs |
| Candidates benchmarked | YOLOX-Nano and S; NanoDet-Plus m and m-1.5x | Apache-2.0 | Not used |
| Excluded | Ultralytics YOLOv5, v8, v11 (AGPL-3.0); YOLOv6, v7, v9 (GPL-3.0); YOLO-NAS (non-commercial weights) | — | Licence |
| Vision (scene descriptions) | Any Ollama-compatible model, through `CC_VISION_MODEL` | Chosen by the operator | Interface tested; **no model evaluated or bundled** |

## 10. Benchmark results
Detail and method: [AI_EVALUATION.md](AI_EVALUATION.md).

| Stage | Data | Result |
|---|---|---|
| Detection (YOLOX-Tiny) | 500 real COCO photographs | AP 0.370, AP50 0.539; precision 0.75 and recall 0.43 at the deployment threshold |
| Detection speed | Production image, one vCPU | 331 ms a frame; at most 79 s per analysis |
| Tracking with the real detector | 24 semi-synthetic sequences: real cut-outs, synthetic motion | 80% of tracked objects keep one identity; 13 of 16 recovered after occlusion |
| Segmentation | 72 held-out synthetic transitions | Precision 1.00, recall 1.00, 0 false a minute, median cut error 0 frames, 70 of 72 typed correctly (before: 0.73 / 0.83) |
| Textured surfaces | 42 synthetic clips | 30 found, up from 3; none invented on bare walls |
| Surface tracking | Synthetic, against ground truth | 4.5 px median worst-corner error at 480 × 270 |
| **Real video** | — | **BLOCKED (B-7)** |

## 11. Test execution evidence
| Suite | Result |
|---|---|
| PostgreSQL 16 | **250 passed** |
| SQLite | **249 passed, 1 skipped** (PostgreSQL-only) |
| After the last refactor, the files it touches | 39 passed on each database |
| Web | `tsc`, ESLint (0 errors), unit tests, audit, build: all pass |
| Browser walks | 12/12 (marketplace, with test-mode payment) and 12/12 |
| Deployment rehearsal | 15/15 |
| Load test, production image, one vCPU | 59 requests a second for 20 users, 0 errors, p95 542 ms |
| Secret scan | 0 findings in 284 files; history has only D-SEC-00 |

Logs are in [docs/qa/evidence/v4/](../qa/evidence/v4/). Every run, including stopped ones and why, is in [REGRESSION_LOG.md](REGRESSION_LOG.md).

## 12. CI/CD workflow results
**Not run.** CI starts on a push to `main`, which only the owner does. The local runs use the same commands, with two differences: Python 3.11 instead of 3.12, and Windows instead of Ubuntu. They are not `CI_VERIFIED`.

## 13. Docker rehearsal results
**15/15** on the final code. The image fetches the detection model at build time and verifies its checksum. In the same image, pinned to one vCPU with 2 GB, a 30 s analysis took 36 s with a peak of 552 MB.

## 14. Deployment verification
**None.** The live API refuses to start without settings (B-2), as it should. After the owner imports them: `python qa/vercel_check.py https://www.visuara.si`.

## 15. Payment verification
| Path | Verified |
|---|---|
| Bank transfer recorded by an administrator | Yes, tests and earlier walks |
| Provider flow in test mode, built-in sandbox | **Yes**: tests (signatures, replays, duplicates, mismatches, strangers, failure then success, ledger invariants) and the browser walk |
| Stripe test mode | **No** (B-4): the adapter is tested against Stripe's documented formats only. The owner's test keys are needed (checklist step 6) |
| Live payments | **No**: refused until the owner sets `CC_PAYMENT_LIVE=1` after the legal review (checklist step 9) |

## 16. Outstanding external dependencies
| ID | What | Who |
|---|---|---|
| D-SEC-00 | Reset the Neon password | Owner |
| B-2 | Import the settings into Vercel | Owner |
| B-3 | Add the Google redirect URI | Owner |
| CI | Push to GitHub | Owner |
| B-4 | Stripe account and test keys, to verify the adapter against Stripe | Owner |
| B-5 | Vercel Pro, for commercial use | Owner |
| B-7 | Licensed real footage, needed for every real-video accuracy claim | Owner |
| B-8 | Legal review | A lawyer |
| D-QA-06 | The container scan runs in CI on the first push | Owner (push) |
| — | A vision model server, if scene descriptions are wanted | Owner |

## 17. Known limitations
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## 18. Remaining technical debt
[docs/audit/TECHNICAL_DEBT.md](../audit/TECHNICAL_DEBT.md):
- M13 resolved debt 21 (no ESLint);
- M14 resolved debt 5 (no detection) and debt 15 (plain surfaces only).

## 19. Recommendation
The steps are in [PRE_PRODUCTION_CHECKLIST.md](PRE_PRODUCTION_CHECKLIST.md).

**Before even a controlled beta,** the owner must:
- reset the Neon password;
- get CI passing on a push;
- import the settings and add the Google redirect URI;
- check the deployment with `qa/vercel_check.py`;
- schedule monitoring.

After that, the product is fit for a **controlled beta**: invited customers, payment by bank transfer or Stripe test mode. The marketplace and the original placement workflow are verified locally end to end, with real detection and tracking, and with payment through the test checkout.

**Before a production release,** four more things are needed:
- Vercel Pro;
- the legal review (B-8);
- Stripe verified in test mode with the owner's keys (B-4), and live payments only by the owner's explicit setting;
- an evaluation on licensed real footage (B-7), before any accuracy is claimed to customers.
