# V8 security review (internal)

Scope: code review of V8 changes plus automated checks run in this environment. This is not an independent audit or penetration test.

## Dependency scan (first run ever; `pip-audit`, 2026-10-30)
| Package | Advisories | Action | Status |
|---|---|---|---|
| Pillow 12.3.0 | 13 (fixed in 13.0.0) | Pinned `Pillow>=13.0.0` for Python ≥ 3.11; the Dockerfile uses 3.12 | **Open locally**: this machine's Python 3.10 cannot install Pillow 13. Untrusted images are still decoded by Pillow 12.3.0 here |
| Starlette 0.50.0 | 6 (fixed by 1.3.1) | Upgraded to FastAPI 0.162.0 + Starlette 1.7.0; full suite re-run | Fixed |
| pytest 9.0.2 | 1 | Upgraded to 9.2.0 | Fixed (dev only) |

Second scan of the **installed environment** (`pip-audit` without `-r`) at the end of V8. It found packages that the requirements-file scan does not cover:

| Package | Advisories | Action | Status |
|---|---|---|---|
| python-multipart 0.0.29 | 3 (fixed in 0.0.30/0.0.31) | Parses every multipart upload. Pinned and installed 0.0.32; API and V8 tests re-run (17 passed), then the full suite | Fixed |
| filelock 4.1.0 | several | Orphan (nothing imports or requires it). The fix needs Python ≥ 3.11 | Open locally, not reachable from the app |
| pip 23.0.1, setuptools 65.5.0 | several | Virtualenv tooling, not runtime | Open locally; the Docker image installs its own |

## New attack surface and controls
| Surface | Control | Evidence |
|---|---|---|
| 3D model upload (GLB) | Binary GLB only; external, data-URI or file URIs rejected (no SSRF or local-file reads); 25 MB, 100k triangles, 4096 px textures; accessor bounds checked; node depth limit; finite-value checks | `test_glb_loader_accepts_valid_and_rejects_adversarial` (external URI, out-of-bounds accessor, bad magic, truncation, non-triangle) |
| Product images | Size, pixel and decompression limits; EXIF/ICC handled on a derived copy; original immutable | `test_prepare_asset_*` |
| Cross-org access | Single choke point (`owned` → `_shared`): video only with an active grant; placements visible to the video's org; products visible only through placements on the viewer's videos. Writes still need role plus rights-holder checks | `test_marketplace_*` (pre-licence 404, rival 404, post-revocation 404/403) |
| Marketplace | Exposes metadata and analysed keyframes only; no stream before licence | same test |
| Licence revocation | Revokes all of the brand's placements on the video, cancels jobs, deactivates links, blocks delivery | same test |
| Signed links | HMAC-SHA256, 300 s TTL, bound to job, user and version; all delivery gates re-checked at use; redacted from access logs; key file created owner-only | `test_signed_download_links` |
| Backups | Hash manifest; restore refuses corrupt or partial sets and non-empty targets; refuses missing DB; signing key excluded | `test_backup_restore_round_trip` |

## Residual risks
- S1 (open): local disk without encryption at rest; no object store.
- S2 (open, local only): Pillow 12.3.0 advisories on Python 3.10.
- S3: expensive endpoints (quality check, especially 3D at ~1–5 s per second of footage) have no per-user rate limits. Risk of CPU exhaustion by authenticated users.
- S4: FFmpeg, OpenCV and the GLB parser run in-process without a sandbox.
- S5: audit events are in the same mutable database.
- S6: signed links are bearer tokens (anyone holding one may download until expiry, subject to live rights checks). They are not single-use.
- S7: marketplace keyframes are visible to every brand organization once a video is listed. Creators are told this in the listing dialog.
