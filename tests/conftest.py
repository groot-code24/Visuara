"""Test-wide safety and shared fixtures.

Never load the developer's .env (it may point at a real Neon database, bucket or mail server). Every test gets a
fresh database: a temp SQLite file, or a new PostgreSQL schema when CC_TEST_DATABASE_URL is set.
"""
import os

os.environ['CC_DOTENV'] = '0'
for _k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_DATABASE_SCHEMA', 'CC_S3_BUCKET', 'BLOB_READ_WRITE_TOKEN', 'CC_WORKER_MODE', 'CC_SMTP_HOST', 'CC_MAIL_BACKEND',
           'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'CC_APP_URL', 'CC_VISION_MODEL', 'CC_DETECTION_MODEL',
           'CC_PAYMENT_PROVIDER', 'CC_PAYMENT_WEBHOOK_SECRET', 'CC_STRIPE_SECRET_KEY', 'CC_PAYMENT_LIVE', 'CC_PROGRAMMATIC_TEST_ADAPTERS',
           'LEMONSQUEEZY_API_KEY', 'LEMONSQUEEZY_STORE_ID', 'LEMONSQUEEZY_WEBHOOK_SECRET', 'LEMONSQUEEZY_VARIANT_PRO_MONTHLY',
           'LEMONSQUEEZY_VARIANT_PRO_ANNUAL', 'LEMONSQUEEZY_VARIANT_AGENCY_MONTHLY', 'LEMONSQUEEZY_VARIANT_AGENCY_ANNUAL', 'CC_LEMONSQUEEZY_API_URL'):
    os.environ.pop(_k, None)
# Workspaces created in tests are on the Team plan unless a test sets another; plan limits have their own tests.
os.environ['CC_DEFAULT_PLAN'] = 'team'

import pytest  # noqa: E402

from app import main, db as dbm  # noqa: E402
from helpers import CSRSTestClient  # noqa: E402


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB', tmp_path / 'test.sqlite3')
    url = os.getenv('CC_TEST_DATABASE_URL')
    if not url:
        monkeypatch.delenv('DATABASE_URL', raising=False); monkeypatch.delenv('CC_DATABASE_URL', raising=False)
        yield None; return
    import psycopg, secrets
    schema = 't_' + secrets.token_hex(6)
    with psycopg.connect(url, autocommit=True) as con: con.execute(f'CREATE SCHEMA {schema}')
    monkeypatch.setenv('DATABASE_URL', url); monkeypatch.setattr(main, 'DB_SCHEMA', schema)
    yield schema
    dbm.close_pools()
    with psycopg.connect(url, autocommit=True) as con: con.execute(f'DROP SCHEMA {schema} CASCADE')


def _app_client(tmp_path, monkeypatch):
    for name, value in (('DATA', tmp_path), ('UPLOADS', tmp_path / 'uploads'),
                        ('OUTPUTS', tmp_path / 'outputs'), ('FRAMES', tmp_path / 'frames')):
        monkeypatch.setattr(main, name, value)
    for p in (main.UPLOADS, main.OUTPUTS, main.FRAMES): p.mkdir(parents=True, exist_ok=True)
    main.init_db()
    return CSRSTestClient(main.app)


@pytest.fixture()
def client(tmp_path, monkeypatch, isolated_db):
    """App client with the inline worker running (renders and decisions happen in the background)."""
    monkeypatch.setenv('CC_INLINE_WORKER', '1')
    with _app_client(tmp_path, monkeypatch) as c:
        yield c


@pytest.fixture()
def manual(tmp_path, monkeypatch, isolated_db):
    """App client without the inline worker, so tests drive claims, renders and decisions deterministically."""
    monkeypatch.setenv('CC_INLINE_WORKER', '0')
    with _app_client(tmp_path, monkeypatch) as c:
        yield c


@pytest.fixture()
def mail(client, monkeypatch):
    """Client whose sign-in emails land in DATA/outbox."""
    monkeypatch.setenv('CC_MAIL_BACKEND', 'outbox')
    return client
