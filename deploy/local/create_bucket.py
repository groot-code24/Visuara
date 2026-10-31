"""Create the private development bucket in the local S3 stand-in (docker-compose s3-init service)."""
import os, time

import boto3

s3 = boto3.client('s3', endpoint_url=os.getenv('S3_URL', 'http://s3:8333'), region_name='us-east-1')
for _ in range(30):
    try: s3.list_buckets(); break
    except Exception: time.sleep(2)
bucket = os.getenv('BUCKET', 'context-commerce-dev')
try: s3.create_bucket(Bucket=bucket)
except Exception as e:
    if 'BucketAlready' not in str(e): raise
# Browsers upload videos straight into the bucket from the web app's origin (presigned PUT).
s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={'CORSRules': [{'AllowedOrigins': [os.getenv('APP_ORIGIN', 'http://localhost:3000')],
                   'AllowedMethods': ['PUT', 'GET', 'HEAD'], 'AllowedHeaders': ['Content-Type'], 'MaxAgeSeconds': 3600}]})
print(f'bucket ready: {bucket}')
