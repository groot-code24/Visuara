"""Container health check for a render worker: exit 0 when this host has a worker heartbeat younger than
CC_WORKER_STALE_SECONDS (default 120). Reads heartbeat files only; never touches the database."""
import json, os, socket, sys, time
from pathlib import Path

if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    data = Path(os.getenv('CC_DATA_DIR', root / 'data')).resolve()
    stale = float(os.getenv('CC_WORKER_STALE_SECONDS', '120'))
    alive = []
    for f in (data / 'run').glob(f'worker-{socket.gethostname()}-*.json'):
        try: d = json.loads(f.read_text())
        except Exception: continue
        if time.time() - d.get('at', 0) <= stale: alive.append(d)
    print(json.dumps({'alive': len(alive)}))
    sys.exit(0 if alive else 1)
