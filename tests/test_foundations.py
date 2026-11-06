"""Phase 1 foundations: ordered migrations, the upgrade of a pre-Phase-1 database, memberships kept in step with users,
video and analysis versions, typed opportunities, the transactional outbox and foreign keys on the new tables."""
import dataclasses, hashlib, json, re, time
from pathlib import Path

import pytest

from app import main, migrations, members, outbox, opportunities, analysis_versions, video_versions, jobrunner, db as dbm
from helpers import TestDB, make_user, login, owner, invite_and_join, member_id, upload, needs_media
from benchmark.fixtures import generate

PHASE1_TABLES = ('outbox_consumptions', 'outbox_events', 'opportunities', 'scene_frames', 'scenes', 'analysis_versions', 'video_versions', 'memberships', 'schema_migrations')


def migration_tables():
    """Every table a migration creates, newest first, so a test can return a database to its pre-Phase-1 state."""
    names = []
    for m in migrations.MIGRATIONS:
        for t in re.findall(r'CREATE TABLE (?:IF NOT EXISTS )?(\w+)', m.sql or ''):
            if t not in names: names.append(t)
    return list(reversed(names)) + ['schema_migrations']


# ---------------------------------------------------------------- migrations
def test_migrations_are_recorded_in_order_and_run_once(manual):
    rows = TestDB().execute('SELECT id, checksum FROM schema_migrations ORDER BY id').fetchall()
    assert [r['id'] for r in rows] == [m.id for m in migrations.MIGRATIONS]
    assert all(r['checksum'] == m.checksum() for r, m in zip(rows, migrations.MIGRATIONS))
    with main.db() as c: assert migrations.apply(c) == []                       # nothing left to apply
    with main.db() as c:
        assert {'kind', 'platform_admin', 'current_version'} <= set(c.columns('organizations')) | set(c.columns('users')) | set(c.columns('videos'))
        assert set(PHASE1_TABLES) <= set(c.tables())


def _invalidate_fingerprint():
    with main.db() as c:
        if c.kind == 'postgres': c.execute("UPDATE schema_fingerprint SET value='older'")


def test_a_changed_applied_migration_stops_start_up(manual, monkeypatch):
    changed = [dataclasses.replace(m, sql=m.sql + '\n-- edited after release') if m.id == '0002_memberships' else m for m in migrations.MIGRATIONS]
    monkeypatch.setattr(migrations, 'MIGRATIONS', changed); _invalidate_fingerprint()
    with pytest.raises(migrations.MigrationError, match='0002_memberships was changed'): main.init_db()


def test_a_database_from_a_newer_version_is_refused(manual):
    with main.db() as c: c.execute("INSERT INTO schema_migrations(id,description,checksum) VALUES('9999_future','from a newer release','x')")
    _invalidate_fingerprint()
    with pytest.raises(migrations.MigrationError, match='9999_future'): main.init_db()


def test_migrations_must_be_unique_and_ordered(manual, monkeypatch):
    monkeypatch.setattr(migrations, 'MIGRATIONS', list(reversed(migrations.MIGRATIONS)))
    with main.db() as c, pytest.raises(migrations.MigrationError, match='in order'): migrations.apply(c)


def test_upgrading_a_pre_phase1_database_backfills_everything(manual):
    """A database as V11 left it: users of every status, videos with and without keyframes, duplicate keyframe seconds."""
    with main.db() as c:
        # Later migrations add tables that reference the Phase 1 ones, so all of them go; PostgreSQL enforces the references.
        cascade = ' CASCADE' if c.kind == 'postgres' else ''
        for t in migration_tables(): c.execute(f'DROP TABLE IF EXISTS {t}{cascade}')
        org = c.execute("INSERT INTO organizations(name,plan) VALUES('Legacy Co','team')").lastrowid
        ins = "INSERT INTO users(email,password_hash,name,role,org_id,member_role,suspended_at,removed_at) VALUES(?,'',?,'brand',?,?,?,?)"
        owner_id = c.execute(ins, ('o@legacy.io', 'O', org, 'owner', None, None)).lastrowid
        susp = c.execute(ins, ('s@legacy.io', 'S', org, 'editor', '2026-01-01 00:00:00', None)).lastrowid
        gone = c.execute(ins, ('r@legacy.io', 'R', org, 'viewer', '2026-01-01 00:00:00', '2026-01-02 00:00:00')).lastrowid
        odd = c.execute(ins, ('x@legacy.io', 'X', org, 'brand', None, None)).lastrowid              # a role value no longer valid
        vins = "INSERT INTO videos(org_id,title,source_path,duration,width,height,fps,license_confirmed,created_by,size_bytes) VALUES(?,?,?,?,640,360,25,1,?,1000)"
        v1 = c.execute(vins, (org, 'Analysed', 'uploads/a.mp4', 10.0, owner_id)).lastrowid
        v2 = c.execute(vins, (org, 'Not analysed', 'uploads/b.mp4', 8.0, owner_id)).lastrowid
        for sec in (2.0, 2.0, 5.5, 9.98):
            c.execute("INSERT INTO frames(video_id,second,image_path,description,score) VALUES(?,?,?,?,0.5)", (v1, sec, f'frames/v{v1}_{int(sec*1000)}.jpg', f'at {sec}'))
        if c.kind == 'postgres': c.execute('DELETE FROM schema_fingerprint')
    main.init_db()
    db = TestDB()
    ms = {r['user_id']: (r['member_role'], r['status']) for r in db.execute('SELECT * FROM memberships WHERE org_id=?', (org,)).fetchall()}
    assert ms == {owner_id: ('owner', 'ACTIVE'), susp: ('editor', 'SUSPENDED'), gone: ('viewer', 'REMOVED'), odd: ('viewer', 'ACTIVE')}
    vv = {r['video_id']: dict(r) for r in db.execute('SELECT * FROM video_versions WHERE org_id=?', (org,)).fetchall()}
    assert set(vv) == {v1, v2} and all(r['version'] == 1 and r['content_sha256'] is None for r in vv.values())
    assert vv[v1]['source_path'] == 'uploads/a.mp4' and vv[v1]['duration'] == 10.0
    av = db.execute('SELECT * FROM analysis_versions WHERE org_id=?', (org,)).fetchall()
    assert [(a['video_id'], a['version'], a['status'], a['video_version_id']) for a in av] == [(v1, 1, 'COMPLETED', vv[v1]['id'])]
    scenes = [(s['scene_index'], s['start_second'], s['end_second'], s['boundary_source']) for s in db.execute('SELECT * FROM scenes WHERE analysis_version_id=? ORDER BY scene_index', (av[0]['id'],)).fetchall()]
    assert scenes == [(0, 2.0, 5.5, 'LEGACY_KEYFRAME'), (1, 5.5, 9.98, 'LEGACY_KEYFRAME'), (2, 9.98, 10.0, 'LEGACY_KEYFRAME')]
    assert db.execute('SELECT COUNT(*) FROM scene_frames WHERE analysis_version_id=? AND representative=1', (av[0]['id'],)).fetchone()[0] == 3
    assert db.execute("SELECT kind FROM organizations WHERE id=?", (org,)).fetchone()['kind'] == 'direct'
    with main.db() as c: assert migrations.apply(c) == []


# ---------------------------------------------------------------- memberships
def test_memberships_follow_every_member_change(manual, monkeypatch):
    monkeypatch.setenv('CC_MAIL_BACKEND', 'outbox'); monkeypatch.setenv('CC_AUTH_RATE_PER_MINUTE', '100')
    c = manual
    owner(c, 'owner@brand.io', 'Brand Co')
    for role in ('viewer', 'editor', 'admin'): invite_and_join(c, 'owner@brand.io', f'{role}@brand.io', role)
    login(c, 'owner@brand.io')
    vw, ed, adm, own = (member_id(f'{r}@brand.io') for r in ('viewer', 'editor', 'admin', 'owner'))
    assert c.patch(f'/api/members/{vw}', json={'member_role': 'editor'}).status_code == 200
    assert c.post(f'/api/members/{ed}/suspend').status_code == 200
    assert c.post(f'/api/members/{vw}/suspend').status_code == 200 and c.post(f'/api/members/{vw}/reactivate').status_code == 200
    assert c.delete(f'/api/members/{ed}').status_code == 200
    assert c.post(f'/api/members/{adm}/transfer-ownership').status_code == 200
    with main.db() as con: assert members.membership_drift(con) == []
    org = TestDB().execute('SELECT org_id FROM users WHERE id=?', (own,)).fetchone()['org_id']
    got = {r['user_id']: (r['member_role'], r['status']) for r in TestDB().execute('SELECT * FROM memberships WHERE org_id=?', (org,)).fetchall()}
    assert got == {own: ('admin', 'ACTIVE'), adm: ('owner', 'ACTIVE'), vw: ('editor', 'ACTIVE'), ed: ('editor', 'REMOVED')}
    with main.db() as con: assert con.execute('SELECT COUNT(*) FROM memberships WHERE org_id=? AND user_id=?', (org, own)).fetchone()[0] == 1


def test_membership_drift_is_detected(manual):
    u = make_user('solo@acme.io')
    with main.db() as c:
        assert members.membership_drift(c) == []
        c.execute("UPDATE users SET member_role='viewer' WHERE id=?", (u['id'],))       # a write that skipped sync_membership
        assert [d['id'] for d in members.membership_drift(c)] == [u['id']]


# ---------------------------------------------------------------- video and analysis versions
@needs_media
def test_uploads_and_analyses_add_versions_and_keep_earlier_ones(manual, tmp_path, monkeypatch):
    owner(manual)
    meta = generate('static', 3, tmp_path)
    vid = upload(manual, meta['path'], 'Kitchen', analyze=False)
    db = TestDB()
    vv = db.execute('SELECT * FROM video_versions WHERE video_id=?', (vid,)).fetchall()
    assert len(vv) == 1 and vv[0]['version'] == 1
    assert vv[0]['content_sha256'] == hashlib.sha256(Path(meta['path']).read_bytes()).hexdigest()
    assert manual.post(f'/api/videos/{vid}/analyze').status_code == 200
    first = db.execute('SELECT * FROM analysis_versions WHERE video_id=?', (vid,)).fetchall()
    assert [(a['version'], a['status'], a['video_version_id']) for a in first] == [(1, 'COMPLETED', vv[0]['id'])]
    scenes1 = [dict(s) for s in db.execute('SELECT * FROM scenes WHERE analysis_version_id=? ORDER BY scene_index', (first[0]['id'],)).fetchall()]
    keyframes = db.execute('SELECT second FROM frames WHERE video_id=? ORDER BY second', (vid,)).fetchall()
    assert [s['start_second'] for s in scenes1] == [k['second'] for k in keyframes]
    assert all(a['end_second'] == b['start_second'] for a, b in zip(scenes1, scenes1[1:]))
    assert all(s['end_second'] > s['start_second'] for s in scenes1)

    assert manual.post(f'/api/videos/{vid}/analyze').status_code == 200            # re-analysis adds a version
    versions = db.execute('SELECT version, status FROM analysis_versions WHERE video_id=? ORDER BY version', (vid,)).fetchall()
    assert [(a['version'], a['status']) for a in versions] == [(1, 'COMPLETED'), (2, 'COMPLETED')]
    assert [dict(s) for s in db.execute('SELECT * FROM scenes WHERE analysis_version_id=? ORDER BY scene_index', (first[0]['id'],)).fetchall()] == scenes1

    def broken(*a, **k): raise RuntimeError('decoder crashed')
    monkeypatch.setattr(main, 'extract_frames', broken)
    assert manual.post(f'/api/videos/{vid}/analyze').status_code == 503
    failed = db.execute("SELECT * FROM analysis_versions WHERE video_id=? AND status='FAILED'", (vid,)).fetchone()
    assert failed['version'] == 3 and json.loads(failed['error_json']) == {'type': 'RuntimeError', 'message': 'decoder crashed'}
    with main.db() as c: assert analysis_versions.latest_completed(c, vid)['version'] == 2

    types = [r['event_type'] for r in db.execute("SELECT event_type FROM outbox_events WHERE aggregate_type='video' AND aggregate_id=? ORDER BY id", (str(vid),)).fetchall()]
    assert types == ['video.version.created', 'analysis.completed', 'analysis.completed', 'analysis.failed']


# ---------------------------------------------------------------- opportunities
@pytest.fixture()
def scene(manual):
    u = make_user('pub@acme.io', org='Publisher Co')
    with main.db() as c:
        vid = c.execute("INSERT INTO videos(org_id,title,source_path,duration,width,height,fps,license_confirmed,created_by,size_bytes) VALUES(?,?,?,6.0,640,360,25,1,?,10)",
                        (u['org_id'], 'Clip', 'uploads/clip.mp4', u['id'])).lastrowid
        video_versions.record_upload(c, vid, u['org_id'], u['id'], 'uploads/clip.mp4', 10, 6.0, 640, 360, 25, 'ab' * 32)
        v = dict(c.execute('SELECT * FROM videos WHERE id=?', (vid,)).fetchone())
        aid = analysis_versions.record_completed(c, v, [(1.0, 'f/a.jpg', 'kitchen counter', .6), (3.0, 'f/b.jpg', 'desk', .4)], u['id'])
        sid = c.execute('SELECT id FROM scenes WHERE analysis_version_id=? AND scene_index=0', (aid,)).fetchone()['id']
    return {'user': u, 'video_id': vid, 'analysis_id': aid, 'scene_id': sid}


def _opp(scene_id, **over):
    d = {'scene_id': scene_id, 'candidate_kind': 'SURFACE', 'candidate_label': 'counter top', 'placement_category': 'HORIZONTAL_SURFACE',
         'geometry': {'frame_second': 2.0, 'bbox': [0.1, 0.6, 0.3, 0.2], 'quad': [[.1, .6], [.4, .6], [.4, .8], [.1, .8]]},
         'start_second': 1.2, 'end_second': 2.8, 'visible_seconds': 1.5, 'feasibility_score': .72,
         'product_compatibility': {'beverage': .75, 'food': .95}, 'brand_safety': 'SAFE', 'rendering_complexity': 'LOW',
         'confidence': .6, 'confidence_factors': {'stability': .8, 'size': .5}, 'explanation': 'Stable horizontal surface for 1.5 s.'}
    d.update(over); return d


def test_a_valid_opportunity_is_stored_with_its_lineage(scene):
    org = scene['user']['org_id']
    with main.db() as c:
        oid = opportunities.record(c, _opp(scene['scene_id']), org)
        row = opportunities.load(c.execute('SELECT * FROM opportunities WHERE id=?', (oid,)).fetchone())
    assert row['validation_status'] == 'VALID' and row['region'] == 'BOTTOM_LEFT' and row['schema_version'] == 1
    assert (row['video_id'], row['analysis_version_id'], row['scene_id']) == (scene['video_id'], scene['analysis_id'], scene['scene_id'])
    assert row['geometry']['bbox'] == [0.1, 0.6, 0.3, 0.2] and row['product_compatibility'] == {'beverage': .75, 'food': .95}
    with main.db() as c:
        oid2 = opportunities.record(c, _opp(scene['scene_id'], brand_safety='NOT_EVALUATED'), org)
        assert c.execute('SELECT validation_status FROM opportunities WHERE id=?', (oid2,)).fetchone()[0] == 'NEEDS_REVIEW'


@pytest.mark.parametrize('over, message', [
    ({'end_second': 3.5}, 'outside the scene'),
    ({'start_second': 2.0, 'end_second': 1.5}, 'end_second must be after'),
    ({'visible_seconds': 5}, 'visible_seconds cannot exceed'),
    ({'geometry': {'frame_second': 2.0, 'bbox': [0.8, 0.1, 0.5, 0.2]}}, 'bbox'),
    ({'geometry': {'frame_second': 0.5, 'bbox': [0.1, 0.1, 0.2, 0.2]}}, 'frame_second must be inside'),
    ({'feasibility_score': 1.4}, 'feasibility_score'),
    ({'product_compatibility': {'beverage': 3}}, 'between 0 and 1'),
    ({'placement_category': 'FLOOR'}, 'placement_category'),
    ({'raw_model_output': {'anything': True}}, 'raw_model_output'),
])
def test_invalid_opportunities_are_refused_and_never_stored(scene, over, message):
    with main.db() as c, pytest.raises(opportunities.OpportunityInvalid, match=message):
        opportunities.record(c, _opp(scene['scene_id'], **over), scene['user']['org_id'])
    assert TestDB().execute('SELECT COUNT(*) FROM opportunities').fetchone()[0] == 0


def test_an_opportunity_cannot_point_at_another_organizations_scene(scene):
    other = make_user('brand@other.io', org='Other Co')
    with main.db() as c, pytest.raises(opportunities.OpportunityInvalid, match='does not exist in this organization'):
        opportunities.record(c, _opp(scene['scene_id']), other['org_id'])


# ---------------------------------------------------------------- outbox
@pytest.fixture()
def consumers():
    names = []
    def reg(event_type, name, fn): outbox.register(event_type, name, fn); names.append(name)
    yield reg
    for n in names: outbox.unregister(n)


def _emit(aggregate_id='1', event_type='analysis.completed', **kw):
    with main.db() as c: return outbox.emit(c, event_type, 'video', aggregate_id, {'video_id': aggregate_id}, org_id=None, **kw)


def _status(event_id): return dict(TestDB().execute('SELECT * FROM outbox_events WHERE event_id=?', (event_id,)).fetchone())


def test_an_event_exists_only_if_its_transaction_commits(manual):
    with pytest.raises(RuntimeError):
        with main.db() as c:
            outbox.emit(c, 'analysis.completed', 'video', '1', {}); raise RuntimeError('the change failed')
    assert TestDB().execute('SELECT COUNT(*) FROM outbox_events').fetchone()[0] == 0
    with main.db() as c, pytest.raises(outbox.UnknownEvent): outbox.emit(c, 'no.such.event', 'video', '1', {})


def test_delivery_retries_and_never_repeats_a_consumer_that_succeeded(manual, consumers):
    seen = {'a': [], 'b': []}; fail = {'b': 1}
    def a(c, ev): seen['a'].append(ev['event_id'])
    def b(c, ev):
        seen['b'].append(ev['event_id'])
        if fail['b']: fail['b'] -= 1; raise RuntimeError('downstream unavailable')
    consumers('analysis.completed', 'test-a', a); consumers('analysis.completed', 'test-b', b)
    eid = _emit()
    assert outbox.deliver() == {'retry': 1}
    s = _status(eid)
    assert s['status'] == 'PENDING' and s['attempts'] == 1 and 'downstream unavailable' in s['last_error'] and s['available_at'] > time.time()
    assert outbox.deliver() == {}                                                    # backing off
    assert outbox.deliver(now=time.time() + outbox.backoff(1) + 1) == {'delivered': 1}
    assert seen == {'a': [eid], 'b': [eid, eid]} and _status(eid)['status'] == 'DELIVERED'
    # a redelivery (for example after a lost lease) runs no consumer twice
    with main.db() as c: c.execute("UPDATE outbox_events SET status='PENDING', delivered_at=NULL, available_at=0 WHERE event_id=?", (eid,))
    assert outbox.deliver() == {'delivered': 1} and seen == {'a': [eid], 'b': [eid, eid]}
    assert TestDB().execute('SELECT COUNT(*) FROM outbox_consumptions WHERE event_id=?', (eid,)).fetchone()[0] == 2


def test_events_of_one_aggregate_are_delivered_in_order_and_dead_letters_park(manual, consumers):
    order = []; broken = set()
    def h(c, ev):
        if ev['event_id'] in broken: raise RuntimeError('cannot process')
        order.append(ev['event_id'])
    consumers('analysis.completed', 'test-order', h)
    first = _emit('1', max_attempts=2); second = _emit('1'); other = _emit('2'); broken.add(first)
    outbox.deliver()
    assert order == [other] and _status(second)['attempts'] == 0                    # held back behind the failing event
    outbox.deliver(now=time.time() + outbox.backoff(1) + 1)                         # first dead-letters, so second follows
    assert _status(first)['status'] == 'DEAD'
    assert order == [other, second] and _status(second)['status'] == 'DELIVERED'


def test_one_delivery_pass_drains_a_chain_of_events(manual, consumers):
    got = []
    consumers('analysis.completed', 'test-chain', lambda c, ev: got.append(ev['event_id']))
    ids = [_emit('7') for _ in range(4)]
    assert outbox.deliver() == {'delivered': 4} and got == ids


def test_a_claimed_event_cannot_be_claimed_again(manual):
    _emit()
    now = time.time()
    assert len(outbox._claim(main.db, now, 10)) == 1 and outbox._claim(main.db, now, 10) == []
    assert len(outbox._claim(main.db, now + outbox.LEASE_SECONDS + 1, 10)) == 1          # an expired lease can be taken over


def test_the_http_runner_delivers_pending_events(manual, consumers):
    got = []
    consumers('analysis.completed', 'test-runner', lambda c, ev: got.append(ev['event_id']))
    eid = _emit()
    assert jobrunner.run_one() == {'ran': 'outbox'} and got == [eid]


# ---------------------------------------------------------------- foreign keys
def test_new_tables_enforce_their_relationships(manual):
    u = make_user('fk@acme.io')
    with pytest.raises(dbm.IntegrityError):
        with main.db() as c:
            c.execute("INSERT INTO scenes(org_id,video_id,video_version_id,analysis_version_id,scene_index,start_second,end_second,boundary_source) VALUES(?,999,999,999,0,0,1,'TEST')", (u['org_id'],))
    with pytest.raises(dbm.IntegrityError):
        with main.db() as c:
            c.execute("INSERT INTO memberships(org_id,user_id,member_role) VALUES(?,?,'boss')", (u['org_id'], u['id']))
