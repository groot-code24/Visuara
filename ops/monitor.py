"""Uptime and alert check for cron or an external scheduler. Exit code 0 = healthy, 1 = alert.

    CC_OPS_TOKEN=... python -m ops.monitor --api https://api.example.com [--webhook https://hooks.slack.com/...]
Checks: liveness, readiness, worker heartbeats, queue backlog and age, 24 h job failure rate, disk space.
Readiness and metrics query the database, so on Neon Free schedule this every 30-60 minutes; a frequent external
uptime monitor should poll only /api/health/live, which never touches the database.
"""
import argparse, json, os, sys, urllib.error, urllib.request

THRESHOLDS = {'queue_depth': 20, 'oldest_queued_age_seconds': 1800, 'failure_rate': 0.25, 'min_free_gb': 2.0}


def get(url, token=None):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'{}')
        except Exception: return e.code, {}
    except Exception as e: return 0, {'error': str(e)[:200]}


def check(api, token):
    alerts = []
    st, _ = get(api + '/api/health/live')
    if st != 200: return [f'backend down (liveness HTTP {st})']
    st, ready = get(api + '/api/health/ready')
    if st != 200: alerts.append(f'backend not ready: {ready.get("checks")}')
    if token:
        st, mtr = get(api + '/api/ops/metrics', token)
        if st != 200: alerts.append(f'metrics unavailable (HTTP {st})')
        else:
            q, j = mtr['queue'], mtr['jobs_24h']
            if mtr['workers']['alive'] < 1: alerts.append('no live render worker')
            if q['depth'] > THRESHOLDS['queue_depth']: alerts.append(f'queue backlog {q["depth"]}')
            if (q['oldest_queued_age_seconds'] or 0) > THRESHOLDS['oldest_queued_age_seconds']: alerts.append(f'oldest queued job waiting {q["oldest_queued_age_seconds"]} s')
            if j['finished'] >= 4 and j['failure_rate'] > THRESHOLDS['failure_rate']: alerts.append(f'job failure rate {j["failure_rate"]:.0%} in 24 h')
            if mtr['disk']['free_gb'] < THRESHOLDS['min_free_gb']: alerts.append(f'disk low: {mtr["disk"]["free_gb"]} GB free')
    return alerts


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--api', required=True); ap.add_argument('--webhook', default=os.getenv('CC_ALERT_WEBHOOK_URL', ''))
    a = ap.parse_args()
    alerts = check(a.api.rstrip('/'), os.getenv('CC_OPS_TOKEN'))
    print(json.dumps({'healthy': not alerts, 'alerts': alerts}))
    if alerts and a.webhook:
        body = json.dumps({'text': 'Visuara alert: ' + '; '.join(alerts)}).encode()
        try: urllib.request.urlopen(urllib.request.Request(a.webhook, data=body, headers={'Content-Type': 'application/json'}), timeout=10)
        except Exception as e: print(f'webhook failed: {e}', file=sys.stderr)
    sys.exit(1 if alerts else 0)
