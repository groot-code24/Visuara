"""Logical export/import between SQLite and PostgreSQL (the SQLite -> Neon migration path), verified per table."""
import os, secrets

import pytest

from app import main, db as dbm
from ops import dbtransfer
from helpers import owner


def test_round_trip_between_engines_preserves_every_row(client, tmp_path):
    b = owner(client, 'brand@example.com', 'Acme Ünïcode')
    assert client.post('/api/invitations', json={'email': 'c@example.com', 'member_role': 'viewer'}).status_code == 200
    src = lambda: main.db()
    dump1 = dbtransfer.export(src, tmp_path / 'dump1')
    assert dump1['tables']['users']['rows'] == 1 and dump1['tables']['invitations']['rows'] == 1

    with pytest.raises(SystemExit):                       # never import into a database that already holds data
        dbtransfer.import_(src, tmp_path / 'dump1')

    sqlite_target = tmp_path / 'migrated.sqlite3'
    to_sqlite = lambda: dbm.connect(sqlite_target, url='', id_tabs=main._ID_TABLES)
    assert dbtransfer.import_(to_sqlite, tmp_path / 'dump1')['verified'] is True

    url = os.getenv('CC_TEST_DATABASE_URL')
    if url:   # SQLite -> fresh PostgreSQL schema (what `migrate` does against Neon), then new ids continue after the old ones
        import psycopg
        schema = 't_mig_' + secrets.token_hex(4)
        with psycopg.connect(url, autocommit=True) as pc: pc.execute(f'CREATE SCHEMA {schema}')
        try:
            dbtransfer.export(to_sqlite, tmp_path / 'dump2')
            to_pg = lambda: dbm.connect(None, url=url, schema=schema, id_tabs=main._ID_TABLES)
            assert dbtransfer.import_(to_pg, tmp_path / 'dump2')['verified'] is True
            with to_pg() as c:
                assert c.execute('SELECT name FROM organizations WHERE id=?', (b['org_id'],)).fetchone()[0] == 'Acme Ünïcode'
                new_id = c.execute("INSERT INTO organizations(name) VALUES('After migration')").lastrowid
                assert new_id > b['org_id']
        finally:
            dbm.close_pools()
            with psycopg.connect(url, autocommit=True) as pc: pc.execute(f'DROP SCHEMA {schema} CASCADE')


def test_tampered_dump_is_refused(client, tmp_path):
    owner(client, 'brand@example.com', 'Acme')
    dbtransfer.export(lambda: main.db(), tmp_path / 'dump')
    f = tmp_path / 'dump' / 'users.jsonl'; f.write_text(f.read_text().replace('brand@example.com', 'evil@example.com'))
    with pytest.raises(SystemExit, match='integrity'):
        dbtransfer.import_(lambda: dbm.connect(tmp_path / 'x.sqlite3', url='', id_tabs=main._ID_TABLES), tmp_path / 'dump')
