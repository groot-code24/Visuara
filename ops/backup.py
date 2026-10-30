"""Backup and restore for a Context Commerce deployment (database + media directory).

  python ops/backup.py backup  --data ./data --out ./backups/2026-10-30
  python ops/backup.py restore --backup ./backups/2026-10-30 --data ./data-restored
  python ops/backup.py verify  --backup ./backups/2026-10-30

Database: with DATABASE_URL set (PostgreSQL/Neon) the backup holds a consistent logical export (ops/dbtransfer.py);
otherwise a consistent SQLite online backup. Restore verifies every hash first, refuses a non-empty target (directory
or database), then rewrites stored absolute media paths from the old data directory to the new one. With object
storage, also enable bucket versioning; on Neon, point-in-time restore remains the first line of recovery
(see docs/v9/production-runtime.md).
"""
import argparse, hashlib, json, shutil, sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from app import db as dbm  # noqa: E402

MEDIA_DIRS = ('uploads', 'outputs', 'frames', 'analyses')
PATH_COLUMNS = [('videos', 'source_path'), ('products', 'image_path'), ('products', 'derived_path'), ('products', 'model_path'),
                ('frames', 'image_path'), ('jobs', 'output_path'), ('placement_analyses', 'track_path')]


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()


def _transfer():
    from ops import dbtransfer
    return dbtransfer


def backup(data, out, db=None, connect=None):
    data, out = Path(data).resolve(), Path(out).resolve()
    if connect is None and db is None and dbm.database_url(): connect = _transfer()._connector()
    if out.exists() and any(out.iterdir()): raise SystemExit(f'Backup target {out} is not empty')
    if connect:
        out.mkdir(parents=True, exist_ok=True)
        dman = _transfer().export(connect, out / 'db')
        files = {f'db/{n}': h for n, h in dman['files'].items()}; files['db/manifest.json'] = _sha(out / 'db' / 'manifest.json')
        kind = 'logical'
    else:
        db = Path(db).resolve() if db else data / 'context_commerce.sqlite3'
        if not db.is_file(): raise SystemExit(f'Database {db} does not exist')   # never back up a silently created empty DB
        out.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True); dst = sqlite3.connect(out / 'context_commerce.sqlite3')
        with dst: src.backup(dst)
        src.close(); dst.close()
        files = {'context_commerce.sqlite3': _sha(out / 'context_commerce.sqlite3')}
        kind = 'sqlite'
    for d in MEDIA_DIRS:
        for f in sorted((data / d).rglob('*')) if (data / d).exists() else []:
            if f.is_file() and not f.name.endswith(('.part.mp4', '.dl')):
                rel = f.relative_to(data).as_posix(); (out / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, out / rel); files[rel] = _sha(out / rel)
    manifest = {'format': 2, 'database': kind, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'source_data_dir': str(data), 'files': files}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


def verify(backup_dir):
    b = Path(backup_dir).resolve(); man = json.loads((b / 'manifest.json').read_text())
    bad = [rel for rel, h in man['files'].items() if not (b / rel).exists() or _sha(b / rel) != h]
    return man, bad


def _relocate(c, old, new):
    for table, col in PATH_COLUMNS:
        if col in c.columns(table):
            for rid, val in [tuple(r) for r in c.execute(f'SELECT id,{col} FROM {table} WHERE {col} IS NOT NULL').fetchall()]:
                if val.startswith(old): c.execute(f'UPDATE {table} SET {col}=? WHERE id=?', (new + val[len(old):], rid))


def restore(backup_dir, data, connect=None):
    b, data = Path(backup_dir).resolve(), Path(data).resolve()
    man, bad = verify(b)
    if bad: raise SystemExit(f'Backup integrity check failed for {len(bad)} file(s): {bad[:5]}')
    if data.exists() and any(data.iterdir()): raise SystemExit(f'Restore target {data} is not empty')
    logical = man.get('database') == 'logical'
    data.mkdir(parents=True, exist_ok=True)
    for rel in man['files']:
        if logical and rel.startswith('db/'): continue
        (data / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(b / rel, data / rel)
    old = man['source_data_dir']
    if logical:
        connect = connect or _transfer()._connector()
        res = _transfer().import_(connect, b / 'db')
        with connect() as c: _relocate(c, old, str(data))
        return {'restored_files': len(man['files']), 'database': 'logical', 'rows': res['rows'], 'verified': res['verified'], 'from': str(b), 'to': str(data)}
    raw = sqlite3.connect(data / 'context_commerce.sqlite3'); raw.row_factory = sqlite3.Row
    with dbm.Connection(raw, lambda: None) as c: _relocate(c, old, str(data))
    ok = raw.execute('PRAGMA integrity_check').fetchone()[0]
    raw.close()
    if ok != 'ok': raise SystemExit('Restored database failed PRAGMA integrity_check: ' + ok)
    return {'restored_files': len(man['files']), 'database': 'sqlite', 'integrity_check': ok, 'from': str(b), 'to': str(data)}


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('backup'); a.add_argument('--data', required=True); a.add_argument('--out', required=True); a.add_argument('--db')
    r = sub.add_parser('restore'); r.add_argument('--backup', required=True); r.add_argument('--data', required=True)
    v = sub.add_parser('verify'); v.add_argument('--backup', required=True)
    args = ap.parse_args()
    if args.cmd == 'backup': print(json.dumps({'files': len(backup(args.data, args.out, args.db)['files'])}))
    elif args.cmd == 'restore': print(json.dumps(restore(args.backup, args.data)))
    else:
        man, bad = verify(args.backup); print(json.dumps({'files': len(man['files']), 'corrupt': bad})); sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
