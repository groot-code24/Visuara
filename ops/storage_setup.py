"""Configure and verify the private media bucket (Cloudflare R2 or any S3-compatible store).

    python -m ops.storage_setup --app-origin https://app.example.com [--apply]
Without --apply it only verifies. With --apply it first sets:
  * CORS: the app origin may PUT (direct uploads) and GET/HEAD (presigned downloads); nothing else.
  * Lifecycle: abort incomplete multipart uploads after 1 day (where the provider supports it).
Verification: object round trip, anonymous read refused, presigned GET works, presigned URL expiry enforced.
"""
import argparse, json, sys, time, urllib.error, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--app-origin', required=True); ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    from app import storage
    if not storage.enabled(): sys.exit('CC_S3_BUCKET is not set')
    s3, b, report = storage.client(), storage.bucket(), {}
    if a.apply:
        try:
            s3.put_bucket_cors(Bucket=b, CORSConfiguration={'CORSRules': [{'AllowedOrigins': [a.app_origin.rstrip('/')], 'AllowedMethods': ['PUT', 'GET', 'HEAD'],
                                                                          'AllowedHeaders': ['Content-Type'], 'ExposeHeaders': ['ETag'], 'MaxAgeSeconds': 3600}]})
            report['cors'] = 'applied'
        except Exception as e: report['cors'] = f'not applied: {str(e)[:160]}'
        try:
            s3.put_bucket_lifecycle_configuration(Bucket=b, LifecycleConfiguration={'Rules': [{'ID': 'abort-incomplete-uploads', 'Status': 'Enabled', 'Filter': {'Prefix': ''},
                                                                                               'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}}]})
            report['lifecycle'] = 'applied'
        except Exception as e: report['lifecycle'] = f'not applied: {str(e)[:160]}'
    key = (storage.key_for(f'uploads/healthcheck-{uuid.uuid4().hex}.txt'))
    s3.put_object(Bucket=b, Key=key, Body=b'ok', ContentType='text/plain')
    report['round_trip'] = s3.get_object(Bucket=b, Key=key)['Body'].read() == b'ok'
    endpoint = (storage.public_client().meta.endpoint_url or '').rstrip('/')
    try:
        urllib.request.urlopen(f'{endpoint}/{b}/{key}', timeout=10); report['anonymous_read_refused'] = False
    except urllib.error.HTTPError as e: report['anonymous_read_refused'] = e.code in (400, 401, 403, 404)
    except Exception as e: report['anonymous_read_refused'] = f'unknown: {str(e)[:80]}'
    url = storage.public_client().generate_presigned_url('get_object', Params={'Bucket': b, 'Key': key}, ExpiresIn=2)
    try: report['presigned_get'] = urllib.request.urlopen(url, timeout=10).read() == b'ok'
    except Exception as e: report['presigned_get'] = f'failed: {str(e)[:80]}'
    time.sleep(4)
    try: urllib.request.urlopen(url, timeout=10); report['presigned_expiry_enforced'] = False
    except urllib.error.HTTPError as e: report['presigned_expiry_enforced'] = e.code in (400, 401, 403)
    s3.delete_object(Bucket=b, Key=key)
    print(json.dumps(report, indent=2))
    ok = all(report[k] is True for k in ('round_trip', 'anonymous_read_refused', 'presigned_get', 'presigned_expiry_enforced'))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
