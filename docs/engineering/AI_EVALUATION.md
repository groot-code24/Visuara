# AI evaluation

**As of 5 November 2026 (M14).** Every figure here comes from a run recorded in `benchmark/results/`. Synthetic footage, real photographs and real video are kept apart, and there are **no results on real video**: that needs licensed, labelled footage (B-7), and the framework for it is ready (`benchmark/real/`, `tests/test_real_footage.py`).

| Kind of evidence | What it shows | Where |
|---|---|---|
| **Real photographs** (COCO val2017, labels CC BY 4.0) | Detector accuracy; detector and tracker together on real object appearances | §2, §4 |
| **Synthetic video** (built by the project, labels exact by construction) | That segmentation, surface detection and plane tracking measure what they claim | §3, §5, §6 |
| **Real video** | — | **BLOCKED** (B-7) |

Hardware: a Windows machine with 8 logical CPUs, and Docker pinned to one CPU with 2 GB (`--cpuset-cpus=0 --memory=2g`) for the deployment target. OpenCV 4.11.0. Inference runs with one OpenCV thread, as on Vercel's single vCPU.

## 1. What runs, and what it records
Each analysis records its models and algorithms in `analysis_versions.model_versions_json`. A typical record:

| Stage | Method | Version recorded |
|---|---|---|
| Object detection | YOLOX-Tiny, ONNX, OpenCV DNN | `yolox-tiny@427cc366d34e` (SHA-256 prefix) |
| Object tracking | IoU and centre-distance gating, constant-velocity prediction, appearance recovery | `iou-alpha-beta-reid-v1` |
| Surface detection | Closed contours, plus quadrilaterals built from straight edges | `opencv-contours-v1+lsd-quads-v1` |
| Surface tracking | KLT optical flow, RANSAC homography, re-seeding | `klt-ransac-homography-v1` |
| Camera motion | Global similarity transform between sampled frames | `global-similarity-flow-v1` |
| Scene setting | From detected objects | `coco-object-context-v1` |
| Vision model | Whatever `CC_VISION_MODEL` names, through `app/vision.py` | the model name, `vision-answer-v1` |
| Scoring | Versioned weights over measured factors | `v2` (v1 still reproduces earlier scores exactly) |

## 2. Object detection: choosing a model
**Candidates.** Only models whose code and weights allow commercial use were considered:
- YOLOX Nano, Tiny and S (Megvii, Apache-2.0);
- NanoDet-Plus m and m-1.5x (Apache-2.0).

Licence exclusions:
- Ultralytics YOLOv5, v8 and v11: AGPL-3.0;
- YOLOv6, v7 and v9: GPL-3.0;
- YOLO-NAS: its weights' licence is non-commercial.

**Method** (`benchmark/evaluate_detection.py`):
- **Accuracy:** COCO-style AP on a fixed, seeded subset of 500 COCO val2017 images (seed 2017). The evaluator, `benchmark/coco_eval.py`, gives exactly the same AP, AP50 and AP75 as the official `pycocotools` on identical detections (0.3096, 0.4739, 0.3279 for YOLOX-Nano). It was run once for that check and is not a dependency.
- **Latency:** median and 95th percentile per image, including pre- and post-processing.
- **Memory:** resident memory.

**Results.** The subset is easier than the full validation set: every model scores 3.7 to 5.2 points above its published full-set AP, in the published order.

| Model | File | AP | AP50 | Published AP (full val) | Precision / recall at 0.35, IoU 0.5 | 1 vCPU median / p95 | 240 frames on 1 vCPU | Memory to load |
|---|---|---|---|---|---|---|---|---|
| yolox-nano | 3.5 MB | 0.310 | 0.474 | 25.8 | 0.75 / 0.36 | 195 / 231 ms | 47 s | 7 MB |
| **yolox-tiny** | 19.3 MB | **0.370** | 0.539 | 32.8 | **0.75** / 0.43 | **331 / 361 ms** | **79 s** | 25 MB |
| yolox-s | 34.2 MB | 0.445 | 0.636 | 40.5 | 0.75 / 0.51 | 1149 / 1453 ms | 276 s | 40 MB |
| nanodet-plus-m-416 | 4.6 MB | 0.348 | 0.503 | 30.4 | 0.65 / 0.42 | 233 / 273 ms | 56 s | 9 MB |
| nanodet-plus-m-1.5x-416 | 9.4 MB | 0.378 | 0.538 | 34.1 | 0.67 / 0.46 | 356 / 480 ms | 85 s | 16 MB |

The 1-vCPU columns come from the production image pinned to one CPU, on 100 images ([docs/qa/evidence/v4/m14_detection_docker_1vcpu.json](../qa/evidence/v4/m14_detection_docker_1vcpu.json)). AP and memory come from the local run on 500 images with one thread (`benchmark/results/detection_local_1thread.json`). The process's peak memory stayed under 420 MB in every run.

**Choice: YOLOX-Tiny.**
- **The budget:** the detection stage may use 100 s of one vCPU per analysis, leaving 200 s of Vercel's 300 s call for the other stages. The analysis samples at most 240 frames.
- **Why Tiny:** it's the most accurate model inside that budget, with the best precision at the deployment threshold, which matters because detections feed brand-safety signals and the scene setting.
- **Why not the others:** NanoDet-1.5x is 0.008 AP better but slower, with a worse tail and lower precision. YOLOX-S would take 276 s.

**What the detector does not do.**
- It detects the 80 COCO classes only. There are no product-specific classes, logos or brands.
- On a typical photograph, recall at the deployment threshold is 0.43: more than half the labelled objects, mostly small ones, are not found.
- It is a ranking aid for context and safety, and is never a guarantee that an object is or isn't present.

## 3. Scene segmentation
**Method.** Colour-histogram change between frames sampled every 0.25 s, normalised for gain. Then:
- **brief changes are dropped:** if the picture comes back by the next sample, it was a flash, not a cut;
- **each boundary is moved to its exact frame** by decoding only the frames between the two samples around it;
- **each boundary is typed:** a hard cut puts the change in one frame, while a dissolve or fade spreads it out or passes through black.

**Evaluation** (`benchmark/evaluate_segmentation.py`, `benchmark/fixtures_cuts.py`): synthetic clips, each with three labelled transitions and labelled distractors (a camera flash, an exposure drift, a fast pan, an object crossing). A boundary is correct within ±2 frames of a cut, or within a gradual transition's span ±2 frames.

| Set | Transitions | Precision | Recall | F1 | False boundaries a minute | Median cut error | Type correct |
|---|---|---|---|---|---|---|---|
| Before M14 (development seeds 1–12) | 36 | 0.73 | 0.83 | 0.78 | 5.6 (5 from flashes) | 2 frames | — |
| M14, development seeds 1–12 | 36 | 1.00 | 1.00 | 1.00 | 0 | 0 frames | 35/36 |
| **M14, held-out seeds 13–36** | **72** | **1.00** | **1.00** | **1.00** | **0** | **0 frames** | **70/72** |

Recall by type on the held-out seeds was 1.00 for each of hard cuts, cuts within the same place, dissolves (0.6 and 1.0 s) and fades through black.

**Read these with care:**
- The method was developed against seeds 1–12. The held-out seeds share the generator, so they test new pictures and motion, not new kinds of transition.
- The two type errors are fades labelled as hard cuts.
- A hard cut into a very dark shot would be labelled gradual. Its position is still correct.
- On the older tracking fixtures (`benchmark/evaluate_intelligence.py`), cut recall stays 0.50: every miss is a 0.2 s closing shot, merged on purpose by `min_scene_seconds`.
- Cost: 0.02 s of processing per second of video.

## 4. Object tracking
**Unit sequences** (`tests/test_tracking_objects.py`): identities are kept through a crossing, a short occlusion is recorded as a gap and never filled in, recovery after a longer occlusion happens by appearance and only by appearance, classes never mix, and fast motion between sparse samples is followed.

**With the real detector** (`benchmark/evaluate_tracking.py`):
- **Set-up:** real objects cut out of COCO photographs with their masks, moved across COCO photographs that contain no labelled object of their own bigger than 1% of the picture.
- **Occlusion:** one object passes behind a strip of another photograph for 2 to 5 samples.
- **Scale:** 24 sequences of 16 samples (a sample is 0.5 s, as in the analysis), 48 objects, tracker defaults.
- **What it is:** real imagery with synthetic motion.

| | Objects | Tracked at all | One identity throughout | Identity switches | Recovered after occlusion | Recall of tracked objects |
|---|---|---|---|---|---|---|
| Sequences 0–11 (used while developing) | 24 | 22 | 68% | 8 | 4 of 7 | 0.63 |
| Sequences 12–23 (held out) | 24 | 19 | 95% | 1 | 9 of 9 | 0.86 |
| **All** | 48 | 41 | **80%** | 9 | **13 of 16** | 0.74 |

**What the numbers say:**
- Seven of the 48 objects were never detected well enough to be tracked; detection is the limit, not linking.
- Tracks that match no inserted object:
  - 58 are objects really present in the photographs (small people, clocks, traffic lights, kites), counted separately;
  - 31 are false tracks over 24 sequences, about one per sequence.
- The two halves differ widely; with about 20 objects each, that's sequence difficulty, not a trend.
- Tuning tracker parameters on the development half moved results by about one object, which is noise, so the defaults were kept.

## 5. Surface detection
On the synthetic tracking fixtures (`benchmark/evaluate_intelligence.py`), a poster is found when a detected surface overlaps it by IoU ≥ 0.5:

| Detector | Posters found (42 clips) | Median best IoU |
|---|---|---|
| Contours only (before M14) | 3 (7%) | 0.00 |
| **Contours plus straight-edge quadrilaterals** (`app/surfaces.py`) | **30 (71%)** | **0.95** |

On the bare-wall clips both find nothing, and no opportunity is created. The new detector costs about 0.3 s per representative frame and runs once per scene. Misses include:
- the deforming clips, correctly, since the surface isn't planar;
- clips whose poster outline is broken in more places than the merging joins.

## 6. Surface tracking
Each surface is followed through its scene by homography and checked against the fixtures' per-frame ground-truth corners:

| Measure | Result |
|---|---|
| Median over clips of the largest corner error | 4.5 px in a 480 × 270 frame |
| Static and smooth camera clips | 1.4 to 6.6 px |
| Motion blur | worst 12.2 px |
| Run stops at a cut | `cut_inside` stops at 1.68 s (cut at 1.6 s); `cut_after` at 3.0 s |
| Run stops when the surface leaves the frame | `left_frame` |
| Run stops behind an occluder | `occluded`, at most 2.5 px of error before stopping |

Before M14 the occluder dragged the homography 129 px off the surface, and the run went on. Every reported frame is a tracked one; nothing is extrapolated.

## 7. Scene understanding
- **Camera motion:**
  - unit-tested on known pans, tilts, zooms and a featureless scene, which reports `UNAVAILABLE`;
  - on the room fixture it measures a zoom of 1.37% a second against the true 1.5%.
- **Lighting:** exposure, colour cast and level change, unit-tested on known frames.
- **Setting:** comes from detected objects. It needs two supporting kinds of object, or one decisive one (a bed, a toilet, an oven) seen with confidence ≥ 0.5. Otherwise it's `UNDETERMINED`, as on every synthetic clip.
- **False detections propagate.** In the browser walk's demonstration room (synthetic), YOLOX-Tiny consistently reads the framed poster as a `tv` or `clock` and the patterned table as a `keyboard` at about 0.5, so the setting reads `workspace`. That walk also exposed a defect, fixed and tested: a detection coinciding with a surface was counted as covering it (`test_an_object_that_is_the_surface_is_not_counted_as_covering_it`).
- **Relations** (on, held by, next to): from box geometry only.
- **Safety signals:**
  - from a blade or a wine glass;
  - a signal can raise a scene to `REVIEW` but never make it `SAFE`.
- **Vision model:**
  - its answers are schema-checked, with timeouts, retries and `UNAVAILABLE` or `FAILED` states (`tests/test_understanding.py`);
  - **no model has been evaluated;** none is bundled and none was available here. With `CC_VISION_MODEL` unset, scenes have no description and the stage is `PARTIAL`.

## 8. Scoring and matching
- **Scoring v2:** reproducible, with every factor's evidence stored. v1 still reproduces earlier scores exactly (`test_scoring_v2_is_reproducible_explained_and_leaves_v1_unchanged`).
- **Matching v2:** a product-to-scene context fit, with reasons for every result. It leaves out items whose rights lapsed after listing and items that hit the brand's excluded terms, including detected objects (`test_matching_uses_scene_context_and_leaves_out_what_it_must`).
- **Not yet evidence:** both scores are heuristic ranking aids. They are not validated against human ratings or campaign outcomes.

## 9. Speed of a whole analysis
| Where | Clip | Result |
|---|---|---|
| **Production image, one pinned vCPU, 2 GB** | 29.6 s, 480 × 270, three scenes | **36 s** end to end (1.22 s per second of video); detection 20.5 s of it (55 frames, 355 ms each); peak memory 552 MB |
| This machine, 8 threads (synthetic benchmark clips) | 3.2–4 s clips | median 1.10 to 1.29 s per second of video, varying about 15% between runs |
| Before M14, without detection | the same clips | median 0.49 |

The cost is bounded. Detection samples at most 240 frames per analysis, about 85 s on one vCPU, and each stage resumes in the next call. Not measured: full-HD footage and the deployed Vercel runner itself (M16).

## 10. Reproduce
```
python ops/fetch_models.py --all                                         # the five candidates, checksum-verified
python -m benchmark.evaluate_detection --images <val2017 subset> --annotations instances_val2017.json
python -m benchmark.evaluate_tracking  --images <val2017 subset> --annotations instances_val2017.json
python -m benchmark.evaluate_segmentation --seeds 1-12
python -m benchmark.evaluate_segmentation --seeds 13-36 --out benchmark/results/segmentation_heldout.json
python -m benchmark.evaluate_intelligence
```
The COCO subset is the first 500 image ids after shuffling the sorted val2017 ids with Python's `random.Random(2017)`. Download those images and `instances_val2017.json` from cocodataset.org. They are not stored in the repository.
