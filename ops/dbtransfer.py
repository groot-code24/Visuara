"""Engine-neutral logical export/import of the Visuara database (SQLite or PostgreSQL/Neon).

  python -m ops.dbtransfer export  --out ./backups/db-2026-10-30            # from DATABASE_URL, else --sqlite
  python -m ops.dbtransfer import  --dump ./backups/db-2026-10-30            # into DATABASE_URL (must be empty)
  python -m ops.dbtransfer migrate --sqlite ./data/context_commerce.sqlite3 # SQLite -> DATABASE_URL, verified
  python -m ops.dbtransfer push-media --data ./data                          # upload local media to CC_S3_BUCKET

Export reads every table inside one read-only, repeatable-read snapshot and writes one JSONL file per table with
a SHA-256 manifest. Import verifies the hashes, refuses a target that already holds data, creates the schema,
inserts rows with their original ids, resets PostgreSQL identity sequences, then compares row counts and
content checksums table by table. On Neon this complements (does not replace) Neon's point-in-time restore.
"""
import argparse, hashlib, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import db as dbm  # noqa: E402

BATCH = 500
NOT_DATA = {'schema_fingerprint', 'schema_migrations'}
SEEDED = ('scoring_configs', 'brand_safety_rules')   # reference data written by migrations   # set up by init_db on each database, so they never travel in a dump


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _connector(sqlite=None, url=None, schema=None):
    from app import main
    ids = frozenset(dbm.id_tables(''.join(main.all_schemas())))
    if url is None and not sqlite: url = dbm.database_url()
    if not url and not sqlite: raise SystemExit('Set DATABASE_URL or pass --sqlite')
    return lambda: dbm.connect(Path(sqlite) if sqlite else None, url=url or '', schema=schema, id_tabs=ids)


def _table_digest(columns, rows):
    """Order-independent content hash (text collation differs between SQLite and PostgreSQL)."""
    h = hashlib.sha256(json.dumps(columns).encode())
    for line in sorted(json.dumps(r, default=str) for r in rows): h.update(line.encode() + b'\n')
    return h.hexdigest()


def _read_tables(c):
    if c.kind == 'postgres': c.raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    else: c.raw.execute('BEGIN')
    out = {}
    for t in c.tables():
        if t in NOT_DATA: continue
        cols = c.columns(t)
        order = 'id' if 'id' in cols else cols[0]
        rows = [list(r) for r in c.execute(f'SELECT {",".join(cols)} FROM {t} ORDER BY {order}').fetchall()]
        out[t] = (cols, rows)
    return out


def export(connect, out):
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()): raise SystemExit(f'Export target {out} is not empty')
    out.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        engine = c.kind; tables = _read_tables(c)
    files, summary = {}, {}
    for t, (cols, rows) in tables.items():
        f = out / f'{t}.jsonl'
        with open(f, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(json.dumps({'columns': cols}) + '\n')
            for r in rows: fh.write(json.dumps(r, default=str) + '\n')
        files[f.name] = _sha(f); summary[t] = {'rows': len(rows), 'digest': _table_digest(cols, rows)}
    manifest = {'format': 'cc-logical-1', 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'source_engine': engine, 'files': files, 'tables': summary}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


def _load(dump):
    d = Path(dump).resolve(); man = json.loads((d / 'manifest.json').read_text())
    bad = [n for n, h in man['files'].items() if not (d / n).exists() or _sha(d / n) != h]
    if bad: raise SystemExit(f'Dump integrity check failed: {bad[:5]}')
    tables = {}
    for name in man['files']:
        lines = (d / name).read_text(encoding='utf-8').splitlines()
        tables[name[:-6]] = (json.loads(lines[0])['columns'], [json.loads(x) for x in lines[1:]])
    return man, tables


def import_(connect, dump):
    from app import main
    man, tables = _load(dump)
    main.init_db(connect)
    with connect() as c:
        for t in ('users', 'organizations', 'videos', 'placements', 'jobs'):
            if c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]: raise SystemExit(f'Target database is not empty ({t} has rows)')
        target_tables = set(c.tables())
        for t in SEEDED:                                  # reference rows the migrations seeded on the fresh target; the dump has the source's
            if t in tables and t in target_tables: c.execute(f'DELETE FROM {t}')
        for t, (cols, rows) in tables.items():
            if t not in target_tables: raise SystemExit(f'Target schema has no table {t}')
            tcols = set(c.columns(t)); keep = [i for i, col in enumerate(cols) if col in tcols]
            names = [cols[i] for i in keep]
            sql = f'INSERT INTO {t}({",".join(names)}) VALUES({",".join("?" * len(names))})'
            for r in rows: c.execute(sql, [r[i] for i in keep])
            if c.kind == 'postgres' and 'id' in tcols and t in dbm.id_tables(''.join(main.all_schemas())):
                c.raw.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {t}")
    return verify_against(connect, man)


def verify_against(connect, man):
    with connect() as c: tables = _read_tables(c)
    report = {}
    for t, s in man['tables'].items():
        cols, rows = tables.get(t, ([], []))
        report[t] = {'rows': len(rows), 'expected_rows': s['rows'], 'match': len(rows) == s['rows'] and _table_digest(cols, rows) == s['digest']}
    bad = [t for t, r in report.items() if not r['match']]
    if bad: raise SystemExit(f'Import verification failed for {bad}: {json.dumps({t: report[t] for t in bad})}')
    return {'tables': len(report), 'rows': sum(r['rows'] for r in report.values()), 'verified': True}


def push_media(data):
    from app import storage
    if not storage.enabled(): raise SystemExit('Set CC_S3_BUCKET (and credentials) first')
    n = 0
    for d in storage.KNOWN_DIRS:
        for f in sorted((Path(data) / d).rglob('*')) if (Path(data) / d).exists() else []:
            if f.is_file() and not f.name.endswith(('.part.mp4', '.dl')): storage.persist(f); n += 1
    return {'uploaded': n, 'bucket': storage.bucket()}


def main_cli():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('export'); e.add_argument('--out', required=True); e.add_argument('--sqlite')
    i = sub.add_parser('import'); i.add_argument('--dump', required=True)
    mg = sub.add_parser('migrate'); mg.add_argument('--sqlite', required=True); mg.add_argument('--keep-dump')
    pm = sub.add_parser('push-media'); pm.add_argument('--data', required=True)
    a = ap.parse_args()
    if a.cmd == 'export': r = export(_connector(sqlite=a.sqlite), a.out); print(json.dumps({'tables': len(r['tables']), 'rows': sum(t['rows'] for t in r['tables'].values())}))
    elif a.cmd == 'import': print(json.dumps(import_(_connector(), a.dump)))
    elif a.cmd == 'migrate':
        if not dbm.database_url(): raise SystemExit('Set DATABASE_URL to the target (e.g. your Neon connection string)')
        import tempfile
        dump = a.keep_dump or tempfile.mkdtemp(prefix='cc-migrate-')
        export(_connector(sqlite=a.sqlite, url=''), dump)
        print(json.dumps({**import_(_connector(), dump), 'dump': dump}))
    else: print(json.dumps(push_media(a.data)))


if __name__ == '__main__':
    main_cli()
