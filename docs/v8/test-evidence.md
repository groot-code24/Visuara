# V8 test evidence

Each result below was observed in this session (2026-10-30) on Windows 11, Python 3.10.11, `.venv`, FFmpeg 8.1 and headless Chrome. Commands are run from the repository root. On Windows, `PYTHONPATH` uses `;` as its separator.

## Automated tests
| Command | Observed |
|---|---|
| Baseline before V8 changes: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider` | 24 passed |
| Final: `PYTHONPATH=. python -m pytest -v -p no:cacheprovider` | **33 passed**, 1 warning, 219 s (test_api 8, test_integrity 15, test_e2e_processes 1, test_v8 9) |
| After the python-multipart upgrade: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_api.py tests/test_v8.py` | 17 passed |
| After the python-multipart upgrade, full: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider -rw` | **33 passed**, 1 warning, 225 s |

New V8 tests (`tests/test_v8.py`):

| Test | What it proves |
|---|---|
| `test_prepare_asset_*` | EXIF orientation, ICC conversion, halo and low-resolution warnings; original kept immutable |
| premultiplied fringe | White-on-white edge: V6 darkens to 224, V8 stays ≥ 250 |
| temporal gains | Gains follow a lighting ramp; clamp and deadband respected |
| `test_glb_loader_accepts_valid_and_rejects_adversarial` | External URI, out-of-bounds accessor, bad magic, truncation and non-triangle mode are all rejected |
| 3D render / validate / preview | TRACKED_3D plan passes the gate, renders and validates on a synthetic clip; preview is watermarked |
| `test_3d_refuses_unobservable_camera` | Top-down clip with no FOV is refused (`FOCAL_UNOBSERVABLE`), not rendered with a guessed focal |
| marketplace licence workflow | Two organizations: listing, campaign, offer, accept, grant-scoped placement, render, delivery; rival org gets 404; takedown revokes and blocks delivery |
| `test_signed_download_links` | Expiry, tampering, wrong user and revoked rights all refused |
| `test_backup_restore_round_trip` | Hash-verified backup; refusal of corrupt sets, non-empty targets and a missing DB; integrity_check after restore |

The one warning is `StarletteDeprecationWarning`: using `httpx` with `starlette.testclient` is deprecated. It affects the test client only, not runtime behaviour.

## Browser QA (headless Chrome, real server, persisted SQLite)
| Command | Observed |
|---|---|
| `PYTHONPATH=. python qa/browser_qa.py` (V6 journey, re-run on the V8 UI) | First run: 10/10 steps passed, but the run **failed** on a console error (`checkHealth` wrote to `#healthState` after the user had left Settings). Fixed in `app/static/index.html` with a null guard. Re-run: **10/10, no console errors** |
| `PYTHONPATH=. python qa/browser_qa_v8.py` | **6/6** steps passed (also re-run after the fix). Screenshots: `docs/qa/v8/01–09`, log: `docs/qa/v8/browser-qa-v8-log.json` |

V8 journey: creator lists a video → a separate brand organization uploads a product and GLB, creates a campaign, sees the marketplace match and makes an offer → creator accepts (licence) → brand builds a 3D placement on the licensed video, quality PASSED, previewed with the exact renderer → consent, approvals, validated 3D render, download, signed share link → campaign report separates measured from unmeasured values; marketplace fits 390 px.

## Benchmarks (synthetic only; no real footage)
| Command | Observed (held-out seeds 2–4) | Output |
|---|---|---|
| `python -m benchmark.run_benchmark --split all` | 42/42 correct, 0/21 FA, 0/21 FR, 21/21 validated | `benchmark/results.json` |
| `python -m benchmark.run_2d_quality --seeds 2 3 4` | ΔE V6 5.50 → V8 4.72 (21 clips, 0 regressions) | `benchmark/results_2d.json` |
| `python -m benchmark.run_3d_benchmark --seeds 2 3 4` | 23/24 correct, 0/12 FA, 1/12 FR, 11/11 validated | `benchmark/results_3d.json` |

Sample sizes: 70 + 35 + 40 synthetic clips (dev plus held-out). **Zero real clips.** Details: [benchmark-report.md](benchmark-report.md).

## Dependency audit
| Command | Observed |
|---|---|
| `python -m pip_audit -r requirements.txt` (start of V8) | 20 advisories: Pillow 12.3.0, Starlette 0.50.0, pytest 9.0.2 |
| `python -m pip_audit` (installed environment, end of V8) | Open: Pillow 12.3.0 (fix 13.0.0, needs Python ≥ 3.11), filelock 4.1.0 (orphan package, not in requirements; fix needs Python ≥ 3.11), pip 23.0.1 and setuptools 65.5.0 (venv tooling only). **python-multipart 0.0.29 → 0.0.32** (3 advisories; it parses every upload): upgraded, pinned, tests re-run |

Not run: an independent penetration test, SAST tooling, or container image scanning (Docker was unavailable).

## Not executed
- PostgreSQL and object-storage tests (no Docker daemon).
- Real-footage evaluation (no authorized footage supplied).
- The Docker image build (Python 3.12 with Pillow 13).
