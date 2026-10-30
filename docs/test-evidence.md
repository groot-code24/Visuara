# Test evidence

Environment: Windows 11, Python 3.10.11 (`.venv`), FFmpeg/FFprobe 8.1, OpenCV 4.11, Chrome (headless, DevTools protocol). Date: 2026-10-30. Every result below was observed in this session; nothing is reported that was not executed.

## Baseline (before changes)
`PYTHONPATH=. python -m pytest -q` → **8 passed**, 5 deprecation warnings.

## Final automated suite
`PYTHONPATH=. python -m pytest -v` → **24 passed** in 98.5 s (1 third-party deprecation warning from Starlette's test client).

| File | Tests | What it proves |
|---|---|---|
| `tests/test_api.py` | 8 | Original suite, updated for the quality gate. The tracking test now uses a planar fixture with ground truth and asserts ≤3 px placement error on every interval frame plus output validation. |
| `tests/test_integrity.py` | 15 | Transition table; renderer routing refusals; quality gate on 7 labelled fixtures; full tracked workflow (unsuitable refusal with evidence, rights-holder enforcement, approval order, rights lock, validated render, channel enforcement, cross-org 404, revision invalidates approvals and old render, full audit trail); track tampering blocked; revocation cancels and blocks; mid-render cancel; lease recovery with a stale worker unable to touch the job; dead-letter; consent reset on term change; Secure cookie; UTF-8 page; invited sign-up. |
| `tests/test_e2e_processes.py` | 1 | **Real processes**: uvicorn API plus a separate `worker.py` over HTTP, nothing mocked. Covers the 16 workflow steps from the brief, then a 24 s 1280×720 render where the worker process is **killed mid-job**; a new worker recovers the lease and completes it (`attempts == 2`, `lease_recovered` event), and the output downloads. |

Changed test expectations (none weakened):
- `test_tracking_quad_validation_and_real_perspective_render`: the old source (`testsrc2`) is not a planar surface, so success on it was the bug. It now asserts correct placement against ground truth.
- The workflow test calls the new mandatory `quality-check` and expects `READY_FOR_RENDER` after brand approval.

## Benchmark
`python -m benchmark.run_benchmark --split all` → test split 42/42 correct, 0 false accepts, 21/21 outputs validated. Details: [visual-quality-benchmark.md](visual-quality-benchmark.md).

## Browser QA
`python qa/browser_qa.py` → **10/10 steps passed, no console errors or uncaught exceptions**. Log: [`qa/browser-qa-log.json`](qa/browser-qa-log.json); screenshots in [`docs/qa/`](qa/).

1. Brand registers through the UI.
2. Brand creates an invitation.
3. Creator registers with the token (this step found bug A13), uploads a real video via the file input, analyses scenes.
4. Brand uploads a product image.
5. Corner editor: the video loads, the product preview sits on the surface, a real pointer drag moves a corner, undo restores it, an arrow key nudges by 0.002. Create, then the quality report shows PASSED with 61/61 frames.
6. Fit review accepted; proposed.
7. Creator consent screen; consent and approve.
8. Brand approves, giving READY_FOR_RENDER; render; the page auto-refreshes to SUCCEEDED with validation PASSED; download returns 200.
9. Audit trail shows DELIVERED.
10. At 390 px: no horizontal page scroll on the placements page or in the editor.

Screenshots were reviewed by eye. One layout defect (navigation clipped at 390 px) was found and fixed.

## Not tested
- Real-world footage; long or high-resolution videos beyond 24 s at 720p; concurrent workers under load.
- PostgreSQL, object storage, backup/restore (not implemented).
- Live generative provider (not configured).
- Screen readers and other assistive technology; Safari or Firefox.
- Dependency vulnerability scan (`pip-audit` not installed in the venv).
