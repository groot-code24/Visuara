# Test strategy

## Layers
| Layer | Where | Runs on |
|---|---|---|
| Unit | Pure functions: segmentation, surface following, score combination, money split, migrations ordering, opportunity schema | every `pytest` run |
| API integration | `tests/test_*.py` through FastAPI's test client against a real database: a fresh SQLite file, or a fresh PostgreSQL schema per test when `CC_TEST_DATABASE_URL` is set | SQLite and PostgreSQL 16 |
| Media integration | Real FFmpeg, FFprobe and OpenCV on synthetic footage generated per test (`benchmark/fixtures*.py`); skipped where those tools are missing (`needs_media`) | both databases |
| End to end, backend | `tests/test_marketplace.py::test_the_whole_marketplace_flow`: analysis through payout, with a real render and its validation | both databases |
| Security | `tests/test_security_marketplace.py`, `tests/test_orgs.py`, `tests/test_platform.py`: isolation, roles, kinds, adversarial input, concurrency | both databases |
| Browser | `qa/browser_qa_web.py`: headless Chrome against the built web app and a real API, the original product flow. `qa/browser_qa_marketplace.py`: the marketplace across a publisher, a brand and a platform administrator on the demo environment, from analysis to accepted delivery | local |
| Deployment rehearsal | `qa/vercel_rehearsal.py`: the Vercel containers with PostgreSQL and a Blob stand-in | local, needs Docker |
| AI evaluation | `benchmark/evaluate_intelligence.py`: cut detection, surface detection and speed against ground truth on synthetic footage | on demand |

## Rules
- **Both databases, every time.** The backend suite runs on SQLite and PostgreSQL, because SQLite accepts SQL that PostgreSQL rejects.
- **Regressions block new work.** A test that passed in the baseline and fails later is fixed first.
- **Expectations change only with a reason.** A test's expectation changes only when [REGRESSION_LOG.md](../execution/REGRESSION_LOG.md) records why the old one was wrong.
- **No mocks of what is being tested.** Rendering, analysis and the database are real in every test that claims them. Only external services are replaced: Google sign-in, the Blob API (`tests/blob_stub.py`) and email (outbox folder).
- **Concurrency is tested with real threads** against the database: the reservation race.

## Commands
```
PYTHONPATH=".;tests" .venv311/Scripts/python.exe -m pytest tests -q                    # SQLite
CC_TEST_DATABASE_URL=postgresql://cc:cc-local-only@127.0.0.1:5433/postgres \
  .venv311/Scripts/python.exe -m pytest tests -q                                           # PostgreSQL
.venv311/Scripts/python.exe -m ruff check app ops worker.py benchmark qa tests
.venv311/Scripts/python.exe -m benchmark.evaluate_intelligence --out benchmark/results/intelligence.json
cd web && npx tsc --noEmit && CC_API_ORIGIN=http://127.0.0.1:18000 npx next build
python qa/browser_qa_web.py --api-port 18000 --web-port 13000
python qa/browser_qa_marketplace.py --api-port 18000 --web-port 13000
```
