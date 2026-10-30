# Day 1 to day 90 roadmap

Exit gates are judged on evidence, not dates. Status reflects the end of the v6 session (2026-10-30).

| Days | Focus | Exit gate | Status |
|---|---|---|---|
| 1–7 | Audit, baseline, graph, contracts, reproducible fixture | System understood, runnable, testable | **Met.** Baseline 8/8; 13 audit findings; synthetic fixture with ground truth |
| 8–14 | Rights and identity | Unauthorized access and invalid transitions rejected | **Met for the single-organization model.** Cross-organization grants outstanding |
| 15–21 | Scene analysis | User can inspect a video and understand suitability | **Met (geometric).** Reason codes, timestamps and next actions in the UI |
| 22–30 | Tracking and visual validation | Supported placements pass; unsupported fail safely | **Met on synthetic data.** Real footage outstanding |
| 31–38 | Rendering | A validated placement produces a real playable video | **Met** |
| 39–45 | Generative provider evaluation | Enabled only if a provider passes live checks | **Gate held:** explicitly unavailable; no provider supplied |
| 46–53 | Brand and creator UX | Main workflow without manual DB changes | **Met** (browser QA). Earnings and pricing outstanding |
| 54–60 | Agency and campaigns | Campaign data scoped; ingestion idempotent | **Partial.** Idempotent imports done; agency not started |
| 61–68 | Production infrastructure | Jobs recover; private assets protected | **Partial.** Job recovery met; PostgreSQL and object storage not started |
| 69–75 | Security and observability | Critical controls pass documented tests | **Partial.** See [security-review.md](security-review.md) |
| 76–82 | Benchmark and UX | Operating envelope documented with measurements | **Partial.** Synthetic envelope documented; real-footage benchmark missing |
| 83–90 | Release candidate | Readiness by evidence | **Not reached.** See [release-readiness.md](release-readiness.md) |

## Five highest-priority remaining tasks
1. **Real-footage labelled benchmark** (G5/G13): 50–100 licensed clips split by creator, a labelling flow built on the corner editor, recalibrated thresholds, false-acceptance rate with confidence intervals.
2. **PostgreSQL + versioned migrations + private object storage with signed URLs** (G12/G3), then move quality analysis onto the job queue.
3. **Backup/restore drill and dependency scanning** (`pip-audit`), plus sandboxed media workers (G12/G13).
4. **Cross-organization licence grants** between separate brand and creator organizations (G2/G10), the prerequisite for agencies and a marketplace.
5. **Free-standing object insertion and realism**: decide whether to build depth- and shadow-aware compositing or integrate and *verify* a generative provider whose outputs can be checked. Until then those scenes stay rejected.
