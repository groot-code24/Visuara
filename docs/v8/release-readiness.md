# V8 release readiness

## Decision: **BLOCKED** (pilot and production)

Mandatory requirements are unmet:

1. **No real-footage evidence.** Every visual quality, false-acceptance and 3D-accuracy number comes from synthetic scenes. The brief requires real footage before quality claims, and none was supplied.
2. **No private object storage or production database.** Local disk and SQLite only; Docker was not running and no cloud credentials were available.
3. **Known-vulnerable image decoder in the local runtime.** Pillow 12.3.0 (13 advisories) remains on this machine's Python 3.10. Fixed only in the Python 3.12 Docker image, which could not be built here.

Use that is acceptable today: **supervised internal demonstrations** on synthetic or self-owned footage, run on a trusted machine.

## Gate summary
| Gate | Status | Basis |
|---|---|---|
| A Technical integrity | CONDITIONAL | Full regression green; Pillow advisory open locally |
| B Visual credibility (2D) | CONDITIONAL | −14% ΔE held-out (synthetic), 0 regressions; real footage missing |
| C 3D correctness | CONDITIONAL | Synthetic: 0/12 false accepts, ≤ 1.04% focal, ≤ 0.86°, ≤ 0.66 px; 1 held-out false rejection; real-footage realism unmeasured |
| D Rights and security | BLOCKED | Isolation, consent, licences, revocation, signed links, backups pass; private object storage missing |
| E Commercial usability | PASS (local) | Two-organization journey verified in the browser on persisted data |

## Distinctions
- **Local development:** works; reproducible from README.
- **Controlled pilot:** needs the real-footage benchmark with human ratings, the 3D parallax fix, Python 3.12 runtime, and a backup drill on the pilot host.
- **Production:** additionally PostgreSQL, object storage with encryption and versioning, per-user rate limits on analysis, sandboxed media workers, payments if fees are collected, and an independent security review.

The remaining gap to world-class quality is explicit: realistic lighting and material response, occlusion, parallax-aware 3D validation, and human-rated realism on real footage.
