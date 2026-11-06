# Implementation progress (V4.0)

The milestones run in the specification's order, M13 to M17. A later milestone starts only when the earlier one's open items are outside the code, meaning they wait on the owner, a credential, hardware or licensing. Each such item is named here and in [DEFECT_REGISTER.md](DEFECT_REGISTER.md).

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `IMPLEMENTED`, `TESTED`, `CI_VERIFIED`, `EXTERNALLY_VERIFIED`, `PRODUCTION_VERIFIED`, `BLOCKED`.

## Phase Zero: baseline
| Item | Status | Evidence |
|---|---|---|
| Repository inspection, git state, previous audit | TESTED | [BASELINE.md](BASELINE.md) |
| Every applicable check run and recorded | TESTED | [BASELINE.md](BASELINE.md); logs in `docs/qa/evidence/v4/` |
| Dependency map and sources of truth | TESTED | [DEPENDENCY_MAP.md](DEPENDENCY_MAP.md), generated from the imports |
| Defect register | IN_PROGRESS (kept current) | [DEFECT_REGISTER.md](DEFECT_REGISTER.md) |

## M13: Release stabilization
| Item | Status | Evidence or blocker |
|---|---|---|
| Credential remediation, repository side | TESTED | Working tree clean; scanner checks history and directories (D-QA-03); `check_env.py` refuses values found in history |
| Credential rotation | **BLOCKED** (owner) | [DEPLOYMENT_RUNBOOK.md §1](DEPLOYMENT_RUNBOOK.md) |
| Authentication and authorization audit | TESTED | [SECURITY_REVIEW.md](SECURITY_REVIEW.md) |
| Cross-tenant review of every route | TESTED | The sweep over 83 identifier routes |
| Production security fixes (D-SEC-01 to 06) | TESTED | One regression test each |
| Containers as non-root (D-SEC-07) | IN_PROGRESS | Needs a Vercel preview deployment to confirm port handling |
| `DATABASE_URL` fails fast; never SQLite in production | TESTED | `test_production_without_a_database_refuses_to_start` (real import in a subprocess) |
| Database TLS required in production | TESTED | D-SEC-05 |
| Google callback | IMPLEMENTED; **BLOCKED** externally | The code sends `CC_PUBLIC_URL + /auth/google/callback`; the owner adds that URI (B-3) |
| SMTP in production | TESTED | Outbox refused in production; TLS required (D-SEC-04); `check_env.py --mail` sends a real test email |
| Live settings in Vercel (B-2) | **BLOCKED** (owner) | — |
| PostgreSQL full suite completed | TESTED | 182 at baseline; **189 passed** on the finished code |
| SQLite full suite | TESTED | **188 passed, 1 skipped** on the finished code |
| Docker deployment rehearsal | TESTED | **15/15**, with TLS stand-ins, the image built from the working tree (D-QA-04, D-QA-05) |
| Browser walks | TESTED | Marketplace 12/12, original product 12/12 |
| Container image scan | NOT_STARTED | D-QA-06: no scanner here; running Trivy was refused by this machine's permission policy. The owner decides |
| ESLint and frontend unit tests in CI | TESTED locally | `npm run lint` (0 errors), `npm test`; CI steps added |
| CI green | **BLOCKED** (owner push) | Nothing here can push |

## M13 exit gate
| Gate | Met? |
|---|---|
| No unresolved reproducible Critical or High security defect | **No.** D-SEC-00 (the leaked Neon password) is open until the owner resets it. Every other High one is fixed and tested |
| Production configuration validated | **No.** It needs the owner's settings (B-2, B-3); `qa/check_env.py` is ready to validate them |
| Full local regression suites pass | **Yes**: PostgreSQL 189, SQLite 188 plus 1 skip, both walks 12/12, the web checks |
| Deployment rehearsal passes | **Yes**: 15/15 |

Everything in M13 that the code can do is done. The gate stays open on three owner actions (reset the Neon password, import the settings, add the Google URI) and on CI, which needs a push.

## M14: Intelligence completion
Started after M13's code work was done; M13's open items are owner actions. Evidence for every figure: [AI_EVALUATION.md](AI_EVALUATION.md).

| Item (specification) | Status | Evidence or blocker |
|---|---|---|
| §8.3 Detection: candidates benchmarked for licence, size, CPU latency, memory and accuracy | TESTED | 5 Apache-2.0 models on 500 COCO images and on one pinned vCPU; evaluator matches `pycocotools` exactly |
| §8.3 Detection: a real model in the pipeline | TESTED | YOLOX-Tiny via OpenCV DNN (`app/detection.py`); checksum-verified; `UNAVAILABLE` with the reason otherwise; `tests/test_detection.py` (real inference) |
| §8.3 Resource limits stated | TESTED | 331 ms a frame on one vCPU, at most 240 frames (79 s) per analysis; a larger model needs a separate worker (KNOWN_LIMITATIONS.md) |
| §8.4 Tracking: identities, continuity, occlusion, recovery, scene boundaries, confidence | TESTED | `app/objtrack.py`; `tests/test_tracking_objects.py`; with the real detector, 80% one identity, 13 of 16 recovered (semi-synthetic) |
| §8.4 Tested against real video clips | **BLOCKED** (B-7) | No licensed footage. Real object appearances were used instead (COCO cut-outs) |
| §8.5 Surfaces: feature matching, homography, plane fitting, temporal tracking, occlusion, stability | TESTED | `app/surfaces.py`: 30 of 42 textured posters (was 3); 4.5 px median worst-corner error; stops with a reason; nothing invented on bare walls |
| §8.5 Real-world surfaces evaluated | **BLOCKED** (B-7) | — |
| §8.6 Vision model behind a stable interface: timeouts, retries, limits, unavailable states | TESTED | `app/vision.py`; `tests/test_understanding.py` |
| §8.6 A vision model evaluated | **BLOCKED** | No model server here and none that fits one vCPU; scenes say so (`PARTIAL`) |
| §8.6 Scene understanding from measurements: camera motion, lighting, setting, relations, safety signals | TESTED | `app/understanding.py`; `tests/test_understanding.py`; pipeline test measures the room clip's zoom within 0.4% a second |
| §8.2 Segmentation: labelled evaluation, ±2-frame tolerance, F1, false-positive rate, gradual transitions, exposure | TESTED | Held-out seeds: precision and recall 1.00, 0 false a minute, cut error 0 frames, 70/72 typed (synthetic); was 0.73 / 0.83 |
| §8.7 Versioned scoring with component scores, confidence, evidence, rejection reasons | TESTED | Scoring v2 (migration 0016); v1 still reproduces earlier scores; rejected candidates listed with reasons |
| §8.8 Product matching with scene context, campaign and publisher restrictions, explanations | TESTED | Matching v2; items with lapsed rights or the brand's excluded terms (objects included) left out |
| §9 Compositing validation | TESTED (existing) | `app/validation.py` already checks decoding, duration, resolution, frame rate, audio, frame count, product region per frame against the expected composite (drift, distortion, jumps) and the scene outside it (black frames, flicker). Unchanged in M14 |
| §16 Real-world evaluation framework | IMPLEMENTED; **BLOCKED** on footage | `benchmark/real/`, `tests/test_real_footage.py` (from Phase 12), plus the new detection, tracking and segmentation benchmarks |
| Frontend workflow | TESTED (type check, lint, browser walk) | The analysis page shows the detection model and speed, rejected candidates, scene facts, object tracks with hidden intervals, and every score component with its evidence |
| Defects found on the way: D-MKT-01 (P1, rights), D-AI-07 (false occlusion), and the thread and lease issues in new code | TESTED | Sale steps re-check rights; `test_inventory_whose_rights_lapse_cannot_be_bought` |

## M14 exit gate
| Gate | Met? |
|---|---|
| Actual inference implementation | **Yes**: YOLOX-Tiny runs in the pipeline; real-inference test |
| Reproducible benchmarks | **Yes**: five benchmark scripts with fixed seeds and recorded results in `benchmark/results/` |
| No fabricated detections | **Yes**: nothing is reported for a missing model, a hidden object, a lost surface or an undetermined setting; tests cover each |
| Validated placement pipeline | **Yes, on synthetic video**: surfaces tracked against ground truth; renders validated as before. Real video: BLOCKED (B-7) |
| Integrated frontend workflows | **Yes**: the analysis page, checked in the browser by the marketplace walk (12/12), which found and verified the fix of D-AI-07 |

## M15: Marketplace completion
| Item | Status | Evidence or blocker |
|---|---|---|
| Brand and publisher workflows | TESTED | Existing flows, plus the browser walk (12/12) through payment |
| Agency workflow: delegated access to client brands | TESTED | `app/agencies.py`; `tests/test_agencies.py` (grant, cap at the grant's role, owner actions refused, immediate revocation) |
| Video version management | TESTED | Library page: versions per video, upload a new version straight to storage (D-MKT-02); `test_a_new_version_uploads_straight_to_blob_too` |
| Rights management | TESTED | Verification by administrators (Phase 6); re-checked at every sale step (D-MKT-01) |
| Payment adapter, provider-independent | TESTED (sandbox) | `app/payments.py`: sandbox and Stripe providers behind one interface |
| Stripe adapter | IMPLEMENTED; **BLOCKED** (B-4) | Request and webhook formats tested against Stripe's documented shapes; not exercised against Stripe: no account |
| Webhooks: signatures, replays, idempotency, mismatches | TESTED | `tests/test_payments.py` |
| Ledger | TESTED | Provider and bank payments post the same transaction; trial balance balanced after every flow |
| Reporting | TESTED (existing) | Campaign report, earnings, ledger; payment events for administrators |
| Live payments | **BLOCKED** (owner: B-4, B-5, B-8, `CC_PAYMENT_LIVE=1`) | Refused without the owner's setting |

### M15 exit gate
| Gate | Met? |
|---|---|
| End-to-end commercial lifecycle works in test mode | **Yes**: from listing to paid invoice through the test checkout to accepted delivery, in the browser (12/12) and in tests |
| Financial invariants pass | **Yes**: balanced trial balance, allocations equal the invoice, receivable back to 0, one ledger transaction per payment whatever the provider sends |
| Tenant isolation passes | **Yes**: the sweep over every identifier route, including M15's |
| Critical browser journeys pass | **Yes**: both walks 12/12 |

## M16: Production hardening
| Item | Status | Evidence or blocker |
|---|---|---|
| Performance: analysis on one vCPU | TESTED | 30 s clip in 36 s, 552 MB peak (M14) |
| Load test | TESTED (one instance) | `qa/load_test.py`: 20 users on one pinned vCPU, 59 requests a second, 0 errors, p95 542 ms; found and fixed D-PERF-01 |
| Security | TESTED | Reviews in SECURITY_REVIEW.md §6–7; container scan added to CI (D-QA-06) |
| Recovery | TESTED | Rehearsal kills the runner mid-render; leases recover; analyses resume; backups and restore (`ops/backup.py`, `tests/test_dbtransfer.py`) |
| Monitoring | TESTED | Metrics for analyses, payments and the ledger; `ops/monitor.py` alerts on them |
| Deployment and operations documentation | TESTED (reviewed) | DEPLOYMENT_RUNBOOK.md §8–10; PRE_PRODUCTION_CHECKLIST.md |
| Containers as non-root (D-SEC-07) | **IN_PROGRESS** | Needs a Vercel preview deployment to confirm port handling |

### M16 exit gate
| Gate | Met? |
|---|---|
| Actual CI green | **No: BLOCKED.** CI runs when the owner pushes |
| Docker rehearsal green | **Yes**: 15/15 on the final code |
| Production configuration verified | **No: BLOCKED** on the owner's settings (B-2, B-3) and the Neon reset (D-SEC-00) |
| All release blockers resolved or explicitly documented | **Yes, documented**: PRE_PRODUCTION_CHECKLIST.md and FINAL_RELEASE_REPORT.md §16 |

## M17: Programmatic foundation
| Item | Status | Evidence or blocker |
|---|---|---|
| Integration contracts (versioned) | TESTED | `visuara-inventory-feed/1`, `visuara-deal/1`, `visuara-delivery-report/1`; the feed is checked against its contract |
| Adapter interfaces | TESTED | Supply, demand, measurement |
| Capability discovery with DESIGNED_ONLY, NOT_CONNECTED, CONNECTED, VERIFIED | TESTED | `GET /api/programmatic/capabilities`, with `external` per integration |
| Test adapters and verification | TESTED | Loopback adapters, `POST /api/admin/programmatic/verify`; `tests/test_programmatic.py` |
| Documentation | TESTED (reviewed) | docs/architecture/PROGRAMMATIC_ARCHITECTURE.md |
| A real partner integration | NOT_STARTED | Needs a partner agreement and sandbox |

### M17 exit gate
| Gate | Met? |
|---|---|
| Stable contracts and verified test integrations | **Yes**: all three test adapters VERIFIED; a broken adapter fails verification |
| No false claims of live external integrations | **Yes**: `external_connections` is 0; test adapters are always `external: false` and refused in production |
