# Existing functionality against the V2.0 specification

Status of each specified capability at commit `3bf569f`, before Phase 1.

- **Verified:** implemented and covered by a passing test or browser run.
- **Partial:** some of it exists; the gap is stated.
- **Missing:** nothing exists.

## Identity, organizations, security (spec §7.1, §12)
| Capability | Status | Evidence and gap |
|---|---|---|
| Google and magic-link sign-in | Verified | `app/auth.py`; `tests/test_auth.py`; browser walkthrough steps 3, 4, 11 |
| Sessions, CSRF, secure cookies, rate limits | Verified | `app/main.py:55-90`, `app/auth.py`; `tests/test_auth.py`, `tests/test_platform.py` |
| Workspace roles (viewer < editor < manager < admin < owner), enforced on the backend | Verified | `app/members.py:22-54`; `tests/test_platform.py` |
| Tenant isolation by `org_id` on every query | Verified for the current tables | `owned()` checks in `app/main.py`; cross-workspace tests in `tests/test_workflow.py` and `tests/test_platform.py` |
| Spec roles: platform admin, brand, publisher, agency | Missing | No organization type and no platform-administrator role |
| One person in several organizations | Missing | `users.org_id` is a single column, and sign-up refuses a second workspace |
| Password handling | Not applicable | No passwords exist, by design |

## Product A: Intelligence (spec §6)
| Capability | Status | Evidence and gap |
|---|---|---|
| Single and multi-file upload, progress, cancel, retry | Verified | `web/components/uploaders.tsx`, `app/uploads.py`; `tests/test_blob.py` (multipart, retry) |
| Content validation (not extension or MIME) | Verified | FFprobe for video (`app/main.py:316`), Pillow `verify()` for images (`app/main.py:449-451`) |
| Size, duration, resolution limits | Verified | `app/limits.py`; `tests/test_platform.py` |
| Duplicate detection | Missing | Videos store no content hash |
| Video deletion | Missing | No delete endpoint for videos or products |
| Video versioning | Missing | One row per upload, no versions |
| Asynchronous analysis with leases, retries, recovery | Verified for decisions and renders | `app/decisions.py:385-430`, `app/main.py:96-125`; `tests/test_decisions.py`, `tests/test_blob.py`; rehearsal step 10 |
| Fine-grained stage tracking | Partial | Decisions report a stage string and progress; there are no stage states as in spec §6.2 |
| Keyframe analysis (`POST /api/videos/{vid}/analyze`) | Partial | Runs inside the request, not as a job, and deletes the previous results (`app/main.py:558`) |
| Metadata extraction | Partial | Duration, size, frame rate and audio; codec and aspect ratio are not stored |
| Scene segmentation | Partial | OpenCV scene-change detection picks up to 8 keyframes; scene start and end times are not stored |
| Object detection and tracking | Missing | No detection model. Surfaces are tracked with optical flow and homography (`app/tracking.py`), but objects are not |
| Environment understanding | Partial | Only with the optional local vision model, as free text |
| Surface understanding | Verified, limited | Contour-based flat quadrilaterals classified upright, horizontal or angled (`app/decisions.py`); synthetic clips only |
| Camera and perspective | Verified, limited | Homography per frame; camera pose for 3D (`app/camera.py`) |
| Occlusion and visibility | Verified, limited | The quality policy checks occlusion, blur, cuts and lighting on every frame (`app/quality.py`) |
| Placement opportunities with explainable scores | Partial | Decisions produce scored, explained options, but only inside `result_json`; there is no opportunity table |
| Deterministic, configurable scoring | Partial | Deterministic and explained; weights are constants in code (`docs/decision-engine.md`) |
| Brand safety | Missing | No content-suitability rules |
| Analysis versioning and model versions | Missing | Re-analysis overwrites; `decisions` records no model or configuration version |
| Scene search (structured or semantic) | Missing | No search over scenes |
| Intelligence interface | Partial | Library, decisions and placements pages; no scene timeline, object view or analysis history |

## Product B: Marketplace (spec §7)
| Capability | Status | Evidence and gap |
|---|---|---|
| Brand and publisher onboarding | Missing | Removed in V11; an older version is in commit `eac6a91` |
| Product catalogue | Partial | Products have an image, category, optional 3D model and hash; no brand profile, draft or published state, or guidelines |
| Rights management | Partial | Upload requires a rights confirmation and optional licence expiry; placements carry channels, territories, dates, consent and revocation (`placement_rights`). There is no publisher-level rights record, evidence, or verification state |
| Advertising inventory and reservations | Missing | |
| Brand-to-scene matching | Partial | The decision engine matches a workspace's own products to its own videos; nothing works across organizations |
| Campaign lifecycle | Missing | Placements have a server-enforced lifecycle (`app/lifecycle.py`), but there are no campaigns |
| Rendering through a stable adapter | Verified | `app/renderers.py`, `app/compositor.py`, `app/render3d.py`; immutable outputs per job; `tests/test_workflow.py`, `tests/test_assets_3d.py` |
| Rendering quality checks | Verified | Frame-by-frame validation blocks download on failure (`app/validation.py`); `tests/test_workflow.py` |
| Approvals bound to version and content | Verified, one party | `placement_approvals`, `placement_versions`; any edit resets approval. Only one workspace approves |
| Commercial workflow, ledger, revenue share | Missing | Plans are billed by invoice outside the app (`ops/plans.py`) |

## Product C: Programmatic (spec §9)
| Capability | Status |
|---|---|
| Interfaces, adapters, dynamic placement | Missing. Nothing is designed yet |

## Platform (spec §11, §15, §16)
| Capability | Status | Evidence and gap |
|---|---|---|
| Consistent error responses | Partial | Most errors are `{code, message}`; some are plain strings |
| Versioned API | Missing | Routes are unversioned (`/api/...`) |
| Reliable events (outbox) | Missing | `events` is an audit log written in the same transaction; nothing consumes it |
| Health and readiness | Verified | `/api/health/live`, `/api/health/ready`; `qa/vercel_check.py` |
| Backups and restore | Verified | `ops/backup.py`, `ops/dbtransfer.py`; `tests/test_dbtransfer.py`, `tests/test_assets_3d.py` |
| Resettable demo environment | Missing | Browser QA builds its own data, but there is no demo seed |
| Real dashboard numbers | Verified | `/api/analytics` and `/api/usage` count persisted rows (`app/main.py:1140`) |
