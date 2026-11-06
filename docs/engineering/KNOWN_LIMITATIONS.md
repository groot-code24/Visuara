# Known limitations

What Visuara does not do yet, or does only partly, as of 5 November 2026 (end of M17). Each line says why and what would change it. Technical debt that doesn't limit a user is in [docs/audit/TECHNICAL_DEBT.md](../audit/TECHNICAL_DEBT.md).

## Product
| Limitation | Effect on a user | Why | What would change it |
|---|---|---|---|
| **Detection knows COCO's 80 everyday classes only**, and finds under half the labelled objects in a typical photograph (recall 0.43 at the deployment threshold) | No logos, brands or product-specific classes; small objects are often missed. Opportunities still come from surfaces; objects add context, occlusion and safety signals | YOLOX-Tiny is the most accurate commercially licensed model that fits one vCPU (AI_EVALUATION.md §2) | A larger model on a separately deployed worker (YOLOX-S fits 2 GB but needs about 276 s per analysis on one vCPU); or a model trained for the product's classes |
| **False detections reach the scene's setting.** On the synthetic room clips the detector reads a framed poster as a `tv` or `clock` and a patterned tabletop as a `keyboard` (confidence about 0.5, consistently), so the setting reads `workspace` | A setting can be wrong; it's shown with the objects it came from, carries 8% of the score, and a surface the detection coincides with is not counted as covered | Detector false positives on unusual textures | Evaluation on real footage (B-7); a higher confidence floor for the setting if real footage shows it's needed |
| **Objects are detected on sampled frames** (every 0.5 s, at most 240 frames per analysis) | In videos longer than 2 minutes the sampling is sparser, so short appearances can be missed and tracks fragment | The 300 s per call and one vCPU | A separate inference worker |
| **Tracking keeps one identity for 80% of objects** on semi-synthetic sequences, and 13 of 16 objects were recovered after an occlusion | An object can appear as two tracks | Detection misses are the main cause (AI_EVALUATION.md §4) | Denser sampling on a separate worker; a learned appearance model |
| **Surfaces:** 30 of 42 textured posters found on synthetic clips; a surface whose outline is broken in many places, or that isn't flat, is not offered | Some real posters and screens will be missed | The detector needs straight, mostly continuous outlines | Evaluation on real footage (B-7) will show which cases matter |
| **Accuracy is measured on synthetic video and real photographs, never on real video** | No evidence yet that the measured accuracy holds on real videos | No licensed real footage (B-7) | The framework is ready (`benchmark/real/`, `tests/test_real_footage.py`); it needs footage with written permission |
| **No vision model is bundled or evaluated.** Without `CC_VISION_MODEL`, scenes have no description; the setting comes from detected objects only | No "what is in this scene" sentence; settings with no recognisable objects are `UNDETERMINED` | No model server is available here; a model large enough to be useful does not fit one vCPU | Run a model server separately and set `CC_VISION_MODEL`; the interface validates its answers. It must then be evaluated |
| **Scores and matches are heuristic ranking aids** | They order opportunities and recommendations, explained; they don't predict campaign results | No outcome data | Human ratings and campaign outcomes, once there are customers |
| **Provider payments are verified against the built-in sandbox only, not against Stripe** | Test-mode payments work end to end with the sandbox; Stripe's own checkout and webhooks haven't been exercised from here | No Stripe account (B-4). The adapter follows Stripe's documented request and signature formats and is tested against them | The owner's test keys, then runbook §8a |
| **Refunds and payouts are manual** | A double payment is flagged for a refund in Stripe; publishers are paid by bank transfer recorded by an administrator | Not automated | Stripe refunds and Stripe Connect payouts through the same adapter |
| **No programmatic partner is connected** | No exchange, SSP, DSP or measurement vendor | Contracts, interfaces, test adapters and verification exist (M17); no partner adapter or agreement | A partner adapter, verified against the partner's sandbox, after the agreement |

## Platform
| Limitation | Why | What would change it |
|---|---|---|
| The API and runner containers run as root | Binding port 80 on Vercel's container runtime | Verify `PORT` handling or capabilities on a preview deployment (D-SEC-07) |
| Rate limits are stored in the database | Enough for one deployment at this scale | A shared store (Redis or the edge) when traffic needs it |
| A whole analysis takes about 1.2 s per second of video on one vCPU (measured on a 30 s, 480 × 270 clip in the production image); full-HD footage and Vercel's own runner haven't been measured | Measured on short, small clips only | M16: benchmarks on the deployed runner |
| Load tested only on one instance with SQLite (59 requests a second for 20 users on one vCPU, p95 542 ms) | Neon over the network and Vercel's scaling not measured | A load test against a preview deployment |
| CI has never run on this code | The owner pushes; nothing here can push | The first push to `main` |
| The Vercel deployment has never served traffic | B-2 | The owner imports the settings |

## Accepted risks
- **`minimatch` advisory** in a development dependency (D-SEC-08): moderate, not shipped. Revisit on 19 November 2026.
- **Ten ESLint warnings** are deliberate:
  - Full page loads after sign-in, sign-out, switching organization and invitations, which clear client state on purpose.
  - Plain `<img>` for previews that need the session cookie, which the Next image optimiser can't send.
