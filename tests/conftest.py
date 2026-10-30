"""Test-wide safety: never load the developer's .env (it may point at a real Neon database, bucket or mail server)."""
import os

os.environ['CC_DOTENV'] = '0'
for _k in ('DATABASE_URL', 'CC_DATABASE_URL', 'CC_DATABASE_SCHEMA', 'CC_S3_BUCKET', 'CC_SMTP_HOST', 'CC_MAIL_BACKEND',
           'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'):
    os.environ.pop(_k, None)
