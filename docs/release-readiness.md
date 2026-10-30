# Release readiness

**Decision (2026-10-30): NOT READY for pilot or production. Ready for a supervised local demonstration with synthetic or self-owned footage.**

A numerical score cannot override a failed critical gate; there is no score here.

| # | Gate | Result | Evidence or blocker |
|---|---|---|---|
| 1 | Reproducible startup | PASS (local) | README commands; the E2E test boots API and worker from scratch |
| 2 | Successful migrations | PARTIAL | Idempotent `ALTER TABLE` on SQLite; no versioned migration tool or PostgreSQL |
| 3 | Tenant isolation | PASS | Cross-organization 404 tests |
| 4 | Rights and consent enforcement | PASS | Rights-holder, consent, lock and revocation tests |
| 5 | Version-bound approval integrity | PASS | Content-hash binding; superseded render refused |
| 6 | Real scene analysis | PASS (geometric) | FFmpeg/OpenCV on real files; no semantic understanding claimed |
| 7 | Candidate compatibility evaluation | PARTIAL | Geometry and photometry automated; product–scene semantic fit is a human judgement only |
| 8 | Safe rejection of unsuitable placements | PASS (synthetic) | 21/21 held-out unsuitable clips rejected |
| 9 | Full-interval temporal validation | PASS | Every interval frame measured; 61/61 in tests |
| 10 | Real video rendering | PASS | Real MP4s rendered by a separate worker process |
| 11 | Output integrity checks | PASS | Frame-level validation of the actual file |
| 12 | Authorized delivery | PASS | Authorized 200; anonymous 401; cross-org 404; superseded 403 |
| 13 | Job recovery | PASS (single host) | Worker killed mid-render and recovered |
| 14 | Secure object storage | **FAIL** | Local disk, not encrypted, no signed URLs |
| 15 | Attribution integrity | PARTIAL | Deduplicated imports; version- and channel-bound links; no windows or refunds |
| 16 | Security tests | PARTIAL | Negative tests pass; no dependency scan, pen test or sandboxing |
| 17 | Labelled quality benchmark | **FAIL for release** | Synthetic only; no real-footage benchmark |
| 18 | Browser end-to-end verification | PASS (Chrome) | 10/10 steps, desktop and 390 px |
| 19 | Backup restoration evidence | **FAIL** | Not implemented or exercised |
| 20 | Documented limitations | PASS | [known-limitations.md](known-limitations.md) |

## What would change the decision
- **Controlled pilot (trusted brand and creator, own footage):** gates 17 (real-footage benchmark with a measured false-acceptance rate) and 19 (backup and restore), plus a dependency scan.
- **Production:** additionally gates 2, 14, 15 and 16 (PostgreSQL migrations, object storage with encryption and signed URLs, sandboxed media workers, independent security review).
