"""Run housekeeping by hand (the worker also runs it on a schedule).

    python -m ops.maintenance cache [--dry-run]   evict cached media already safe in the bucket; remove stale temp files
    python -m ops.maintenance db                  expire abandoned uploads, prune expired sessions and challenges
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('what', choices=['cache', 'db']); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    from app import maintenance
    out = maintenance.run_cache_maintenance(dry_run=a.dry_run) if a.what == 'cache' else maintenance.run_db_maintenance()
    print(json.dumps(out, indent=2)); sys.exit(0 if out is not None else 1)
