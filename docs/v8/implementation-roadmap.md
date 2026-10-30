# Day 1–90 roadmap with actual status (V8)

Calendar targets are not evidence. Status reflects what was verified in this session (2026-10-30).

| Phase | Days | Status | Evidence or blocker |
|---|---|---|---|
| 1 Foundation and baseline | 1–7 | **Done** | 24/24 baseline; audit; graph; fixtures reproduce bit-exactly. Day 4 (customer interviews, market facts) **not done**: no customer access; no market claims made |
| 2 2D visual quality | 8–14 | **Done (synthetic)** | Asset prep, premultiplied linear compositing, temporal colour matching; held-out −14% ΔE, 0 regressions |
| 3 Scene intelligence | 15–21 | **Mostly done** | Cut detection, feasibility policy, reason codes, routing existed (V6) and were extended. Automatic *semantic* surface detection not done |
| 4 Real 3D pipeline | 22–33 | **Done for one capability subset (synthetic)** | GLB ingestion, camera estimation, pose, rasteriser, shadows, 3D gate, benchmark vs true camera. Depth and occlusion: refusal only (no depth/segmentation model available offline) |
| 5 Real-footage validation | 34–44 | **Blocked** | Protocol, data pipeline, negatives and reviewer tooling exist for synthetic data. No authorized real footage supplied, so no human rubric scores and no real-world calibration |
| 6 Commercial workflows | 45–55 | **Done (core)** | Separate orgs, listings, campaigns, fixed-price offers, grants, conflicts, brand safety, delivery, takedown; browser-verified. Brand guidelines and product-fidelity requirements: not modelled. Revision requests: via reject + revise |
| 7 Infrastructure and trust | 56–65 | **Partial** | Signed links, backup/restore, tenant-isolation tests, queue reliability done. PostgreSQL and object storage **blocked** (no Docker). Pillow upgrade blocked on Python 3.10 |
| 8 Measurement | 66–73 | **Partial** | Delivery records, exposure seconds (documented method), tracked links, reports with measured vs not measured, CSV. No ad-platform integration, no variant comparison UI, no payments (records only) |
| 9 Polish and operations | 74–83 | **Partial** | Responsive checks at 390 px, keyboard-operable editor, labelled controls. No design-system audit, no assistive-technology testing, no structured JSON logs or correlation IDs |
| 10 Final validation | 84–90 | **Done for available scope** | Full regression, all three benchmarks, browser QA, failure tests, release decision **BLOCKED** |

## Next three highest-priority actions
1. **Real-footage benchmark** (unblocks Gates B, C and E for pilot): 50–100 authorized clips across creators and devices, split by creator, annotated with the corner editor. Recalibrate the planar and 3D policies on dev clips only. Add a human rating rubric for realism (lighting match, edges, shadows, temporal stability, fidelity).
2. **Foreground and parallax handling for 3D:** replace the single-homography foreground check with depth- or segmentation-aware motion analysis, so walls and depth behind the object stop causing rejections. It currently over-rejects (one held-out false rejection).
3. **Production storage and runtime:** PostgreSQL with versioned migrations, an S3-compatible private store with signed URLs, and a Python 3.12 runtime (patched Pillow). Then run the backup and restore drill against them.
