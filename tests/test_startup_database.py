"""The API with an unreachable database (seen in production on 6 Nov 2026: every cold start exited with only the pool's
"couldn't get a connection after 10.00 sec", so even the liveness check failed and sign-in showed "can't reach the
sign-in service"). Now the process starts, says why, refuses other requests with 503, and recovers by itself."""
import json, os, subprocess, sys
from pathlib import Path

import pytest

from app import db as dbm

ROOT = Path(__file__).resolve().parent.parent


def test_connection_failures_are_explained_without_secrets():
    class E(Exception): pass
    cases = {
        'connection failed: FATAL:  password authentication failed for user "neondb_owner"': 'refused the password',
        'could not translate host name "ep-x.neon.tech" to address': 'host name in DATABASE_URL does not exist',
        'ERROR: The endpoint has been disabled. Enable it using Neon API and retry.': 'Neon refused the endpoint',
        'connection timeout expired': 'did not answer in time',
        'connection to server at "127.0.0.1", port 1 failed: Connection refused': 'nothing is listening',
        'FATAL: database "nope" does not exist': 'does not exist',
    }
    for msg, expected in cases.items():
        reason = dbm.explain(E(msg))
        assert expected in reason, (msg, reason)
        assert 'neondb_owner' not in reason and 'ep-x' not in reason and '127.0.0.1' not in reason


_PROBE = r'''
import json, os, sys
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from app import main
c = TestClient(main.app)
out = {'live': c.get('/api/health/live').status_code}
r = c.get('/api/auth/providers'); out['providers'] = [r.status_code, r.json()]
r = c.get('/api/health/ready'); out['ready'] = [r.status_code, r.json()]
if os.getenv('FIX_URL'):
    os.environ['DATABASE_URL'] = os.environ['FIX_URL']; main._DB_STATE['tried'] = 0
    r = c.get('/api/auth/providers'); out['after_fix'] = r.status_code
print('RESULT' + json.dumps(out))
'''


def _run(tmp_path, url, fix=None):
    env = dict(os.environ, DATABASE_URL=url, CC_DATA_DIR=str(tmp_path), CC_DOTENV='0', CC_INLINE_WORKER='0', CC_DB_STARTUP_WAIT='0',
               CC_DB_CONNECT_TIMEOUT='3', CC_DB_POOL_TIMEOUT='3', PYTHONIOENCODING='utf-8')
    env.pop('CC_DATABASE_URL', None)
    if fix: env['FIX_URL'] = fix
    p = subprocess.run([sys.executable, '-c', _PROBE], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    line = next((x for x in p.stdout.splitlines() if x.startswith('RESULT')), None)
    assert line, p.stdout[-2000:] + p.stderr[-2000:]
    return json.loads(line[6:]), p.stdout + p.stderr


def test_the_api_starts_and_says_why_when_nothing_answers_at_the_database_address(tmp_path):
    out, logs = _run(tmp_path, 'postgresql://u:secret-pw-123@127.0.0.1:1/db?sslmode=disable')
    assert out['live'] == 200                                                               # the process is up
    assert out['providers'][0] == 503 and out['providers'][1]['detail']['code'] == 'DATABASE_UNAVAILABLE'
    problem = out['ready'][1]['checks']['database_problem']
    assert out['ready'][0] == 503 and ('nothing is listening' in problem or 'did not answer in time' in problem)   # Windows times out on a closed port
    assert 'database unavailable' in logs and 'secret-pw-123' not in logs                    # the log says why, without the password


@pytest.mark.skipif(not os.getenv('CC_TEST_DATABASE_URL'), reason='needs the test PostgreSQL (CC_TEST_DATABASE_URL)')
def test_a_wrong_password_is_named_and_fixing_the_url_recovers_without_a_restart(tmp_path):
    good = os.environ['CC_TEST_DATABASE_URL']
    user_part, host_part = good.split('@', 1)
    bad = user_part.rsplit(':', 1)[0] + ':wrong-' + 'password@' + host_part
    out, logs = _run(tmp_path, bad, fix=good)
    assert out['live'] == 200 and out['providers'][0] == 503
    assert 'refused the password' in out['ready'][1]['checks']['database_problem']
    assert out['after_fix'] == 200                                                          # recovered on a later request
    assert 'wrong-password' not in logs
