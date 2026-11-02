"""Decision engine: surface orientation, exact assignment, the end-to-end run on synthetic room clips (which product
goes where, explained), applying a recommendation through quality check, approval and render, and the API's limits."""
import pytest

from app import main, decisions
from benchmark import fixtures_scene as scene
from helpers import TestDB, make_user, login, owner, upload, add_product, needs_media, wait_job


def test_orientation_of_surfaces():
    assert decisions.orientation([[100, 50], [300, 55], [300, 200], [100, 205]]) == 'vertical'           # wall poster
    assert decisions.orientation([[150, 250], [430, 250], [490, 360], [90, 360]]) == 'horizontal'        # receding tabletop
    assert decisions.orientation([[100, 80], [300, 20], [370, 240], [150, 200]]) == 'angled'


def _video(vid, **scores):
    return {'video_id': vid, 'options': [{'product_id': p, 'score': s, 'passes_quality': ok} for p, (s, ok) in scores.items()]}


def test_assignment_is_exact_spreads_products_and_skips_weak_fits():
    a, b = 1, 2
    vids = [_video(10, **{str(a): (.9, True), str(b): (.85, True)}), _video(11, **{str(a): (.8, True), str(b): (.6, True)})]
    for v in vids:
        for o in v['options']: o['product_id'] = int(o['product_id'])
    # Greedy would give video 10 product a (.9) and video 11 product b (.6) = 1.5; the exact optimum is b->10, a->11 = 1.65.
    assert decisions.assign(vids, [a, b]) == [b, a]
    assert decisions.assign(vids, [a, b], spread=False) == [a, a]
    weak = [_video(12, **{'1': (.5, True), '2': (.95, False)})]
    for o in weak[0]['options']: o['product_id'] = int(o['product_id'])
    assert decisions.assign(weak, [1, 2]) == [None]                       # below the threshold or failing the quality check
    three = [_video(i, **{'1': (.9, True)}) for i in range(3)]
    for v in three:
        for o in v['options']: o['product_id'] = int(o['product_id'])
    assert decisions.assign(three, [1, 2]) == [1, 1, None]                 # spread: at most ceil(3/2)=2 uses of one product


@pytest.fixture()
def room(tmp_path, monkeypatch):
    monkeypatch.setenv('CC_DECISION_WINDOW_SECONDS', '2.5')
    monkeypatch.setenv('CC_DECISION_CANDIDATES_PER_VIDEO', '3')
    clips = tmp_path / 'clips'
    return {'room': scene.generate('room', 1, clips)['path'], 'room2': scene.generate('room', 2, clips)['path'],
            'bare': scene.generate('bare', 3, clips)['path'],
            'tech': scene.product(clips / 'sleeve.png', 160, 100, (180, 80, 30)), 'food': scene.product(clips / 'granola.png', 120, 90, (40, 160, 220))}


def _run(c, body):
    r = c.post('/api/decisions', json=body)
    assert r.status_code == 200, r.text
    did = r.json()['id']
    assert decisions.run_next() is True
    d = c.get(f'/api/decisions/{did}').json()
    assert d['status'] == 'SUCCEEDED', d.get('error')
    return did, d


@needs_media
def test_decision_engine_end_to_end(manual, tmp_path, room):
    c = manual
    owner(c)
    tech = add_product(c, tmp_path, 'Laptop sleeve', 'tech', room['tech'])
    food = add_product(c, tmp_path, 'Granola', 'food', room['food'])
    v_room = upload(c, room['room'], 'Studio walkthrough', analyze=False)          # the engine analyses keyframes itself
    v_bare = upload(c, room['bare'], 'Empty room', analyze=False)
    did, d = _run(c, {'name': 'Launch', 'product_ids': [tech, food], 'video_ids': [v_room, v_bare]})
    res = d['result']
    assert res['engine_version'] == decisions.ENGINE_VERSION and abs(sum(res['weights'].values()) - 1) < 1e-9
    by_vid = {v['video_id']: v for v in res['videos']}
    rec = by_vid[v_room]['recommendation']
    assert by_vid[v_room]['status'] == 'assigned' and rec['passes_quality'] and rec['score'] >= decisions.MIN_RECOMMEND_SCORE
    assert len(rec['reasons']) >= 4 and rec['preview_url'] and rec['end_second'] - rec['start_second'] >= decisions.min_seconds() - .05
    assert {o['product_id'] for o in by_vid[v_room]['options']} == {tech, food}       # every product scored on this video
    assert by_vid[v_bare]['status'] == 'no_fit' and by_vid[v_bare]['recommendation'] is None and by_vid[v_bare]['explanation']
    assert res['summary'] == {'videos': 2, 'assigned': 1, 'products_used': [rec['product_id']],
                              'unused_products': [p for p in (tech, food) if p != rec['product_id']]}
    pre = c.get(rec['preview_url'])
    assert pre.status_code == 200 and pre.content[:2] == b'\xff\xd8'
    assert c.get('/api/decisions').json()[0]['summary']['assigned'] == 1

    # Applying the recommendation gives a draft placement whose own quality check agrees with the engine.
    ap = c.post(f'/api/decisions/{did}/apply', json={'video_id': v_room})
    assert ap.status_code == 200 and ap.json()['created'] is True
    pl = ap.json()['placement_id']
    assert c.post(f'/api/decisions/{did}/apply', json={'video_id': v_room}).json() == {'placement_id': pl, 'created': False}
    assert c.post(f'/api/decisions/{did}/apply', json={'video_id': v_bare}).status_code == 409
    q = c.post(f'/api/placements/{pl}/quality-check').json()
    assert q['status'] == 'PASSED', q['failures']
    assert c.get(f'/api/decisions/{did}').json()['result']['videos'][0]['recommendation']['placement_id'] == pl
    # Every option's verdict is the one its placement's own quality check reaches (same geometry, same frames).
    for o in by_vid[v_room]['options']:
        if o['product_id'] == rec['product_id']: continue
        other = c.post(f'/api/decisions/{did}/apply', json={'video_id': v_room, 'product_id': o['product_id']}).json()['placement_id']
        oq = c.post(f'/api/placements/{other}/quality-check').json()
        assert (oq['status'] == 'PASSED') == o['passes_quality'], (o['failure_codes'], oq['failures'])
        assert oq['frames_in_interval'] == round((o['end_second'] - o['start_second']) * 25) + 1
    assert c.post(f'/api/placements/{pl}/approve').json()['status'] == 'APPROVED'
    assert c.post(f'/api/placements/{pl}/render').status_code == 200
    jid, token = main.claim_next_job(); main.render_job(jid, token)
    job = next(j for j in c.get('/api/jobs').json() if j['id'] == jid)
    assert job['status'] == 'SUCCEEDED' and job['validation']['status'] == 'PASSED', job['error']


@needs_media
def test_two_products_two_videos_are_spread_with_context(manual, tmp_path, room):
    c = manual
    owner(c)
    tech = add_product(c, tmp_path, 'Laptop sleeve', 'tech', room['tech'])
    food = add_product(c, tmp_path, 'Granola', 'food', room['food'])
    vids = [upload(c, room['room'], 'Room A', analyze=False), upload(c, room['room2'], 'Room B', analyze=False)]
    _, d = _run(c, {'product_ids': [tech, food], 'video_ids': vids})
    recs = [v['recommendation'] for v in d['result']['videos']]
    assert all(recs) and {r['product_id'] for r in recs} == {tech, food}            # each product placed once
    for v in d['result']['videos']:
        for o in v['options']:
            assert set(o['components']) == set(decisions.WEIGHTS) and all(0 <= x <= 1 for x in o['components'].values())


def test_decision_requests_are_validated_and_plan_limited(manual, tmp_path, monkeypatch):
    c = manual
    u = owner(c, plan='free')
    other = make_user('x@other.io', org='Other')
    with main.db() as con:
        pids = [con.execute("INSERT INTO products(org_id,name,image_path,created_by) VALUES(?,?,?,?)", (u['org_id'], f'p{i}', 'x', u['id'])).lastrowid for i in range(5)]
        vids = [con.execute("INSERT INTO videos(org_id,title,source_path,duration,width,height,license_confirmed,created_by) VALUES(?,?,?,?,?,?,1,?)",
                            (u['org_id'], f'v{i}', 'x', 5, 320, 240, u['id'])).lastrowid for i in range(4)]
        foreign = con.execute("INSERT INTO products(org_id,name,image_path,created_by) VALUES(?,?,?,?)", (other['org_id'], 'theirs', 'x', other['id'])).lastrowid
    assert c.post('/api/decisions', json={'product_ids': pids, 'video_ids': vids[:1]}).status_code == 422          # at most 4 products
    r = c.post('/api/decisions', json={'product_ids': pids[:2], 'video_ids': vids})
    assert r.status_code == 402 and r.json()['detail']['code'] == 'PLAN_VIDEOS_PER_DECISION'                   # Free: 3 videos
    assert c.post('/api/decisions', json={'product_ids': [foreign], 'video_ids': vids[:1]}).status_code == 404
    first = c.post('/api/decisions', json={'product_ids': pids[:2], 'video_ids': vids[:1]}).json()
    assert c.post(f'/api/decisions/{first["id"]}/cancel').json()['cancel_requested'] is True
    assert c.get(f'/api/decisions/{first["id"]}').json()['status'] == 'CANCELLED'
    for _ in range(4): c.post(f'/api/decisions/{c.post("/api/decisions", json={"product_ids": pids[:1], "video_ids": vids[:1]}).json()["id"]}/cancel')
    r = c.post('/api/decisions', json={'product_ids': pids[:1], 'video_ids': vids[:1]})
    assert r.status_code == 402 and r.json()['detail']['code'] == 'PLAN_DECISIONS_LIMIT'                       # Free: 5 a month
    usage = c.get('/api/usage').json()
    assert usage['plan'] == 'free' and usage['usage']['decisions_this_month'] == 5
    # Viewers can read decisions but not start them; other workspaces cannot see them.
    make_user('viewer@acme.io', member_role='viewer', org_id=u['org_id']); login(c, 'viewer@acme.io')
    assert c.get('/api/decisions').status_code == 200
    assert c.post('/api/decisions', json={'product_ids': pids[:1], 'video_ids': vids[:1]}).json()['detail']['code'] == 'ROLE_FORBIDDEN'
    login(c, 'x@other.io')
    assert c.get(f'/api/decisions/{first["id"]}').status_code == 404 and c.get('/api/decisions').json() == []


def test_categories_endpoint(client):
    cats = client.get('/api/categories').json()
    assert {c['id'] for c in cats} == set(decisions.CATEGORIES) and all(c['label'] for c in cats)


def test_stalled_decision_is_requeued_then_failed(manual):
    import time
    u = owner(manual)
    with main.db() as con:
        did = con.execute("INSERT INTO decisions(org_id,product_ids,video_ids,created_by,status,attempts,lease_until,lease_token) VALUES(?,'[1]','[1]',?,'RUNNING',1,?,'dead')",
                          (u['org_id'], u['id'], time.time() - 1)).lastrowid
        claimed, token = decisions.claim(con)                        # the crashed worker's decision is requeued and claimed again
    assert claimed == did and token != 'dead'
    TestDB().execute('UPDATE decisions SET lease_until=? WHERE id=?', (time.time() - 1, did))
    with main.db() as con: assert decisions.claim(con) == (None, None)
    row = TestDB().execute('SELECT status,error FROM decisions WHERE id=?', (did,)).fetchone()
    assert row['status'] == 'FAILED' and 'stopped repeatedly' in row['error']


def test_a_lease_is_renewed_during_slow_work_and_not_after_it(manual, monkeypatch):
    import time
    u = owner(manual); monkeypatch.setattr(main, 'LEASE_SECONDS', 0.3)
    with main.db() as con:
        did = con.execute("INSERT INTO decisions(org_id,product_ids,video_ids,created_by,status,attempts,lease_until,lease_token) VALUES(?,'[1]','[1]',?,'RUNNING',1,?,'tok')",
                          (u['org_id'], u['id'], time.time() + 0.3)).lastrowid
    lease = lambda: TestDB().execute('SELECT lease_until FROM decisions WHERE id=?', (did,)).fetchone()['lease_until']
    with main.keep_lease('decisions', did, 'tok', 60):
        time.sleep(1.2)
        assert lease() > time.time()                                          # would have expired twice over without renewal
    time.sleep(1.0)
    assert lease() < time.time()                                              # a finished or dead runner stops renewing
