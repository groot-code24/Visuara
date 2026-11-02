"""qa/vercel_rehearsal.py only, mounted into the container and never shipped: the real application, with Blob's delivery
host (fixed in Vercel's SDK) pointed at the local stand-in. Servers use the Docker address and browsers the local one."""
import os
from urllib.parse import quote

from vercel.blob._internal import service

from app import blobstore

_server, _browser = os.environ['SIM_BLOB_ORIGIN'], os.environ['SIM_BLOB_PUBLIC_ORIGIN']
service.construct_delivery_url = lambda store, pathname, access: f'{_server}/delivery/{quote(pathname, safe="/")}'
blobstore.delivery_url = lambda pathname: f'{_browser}/delivery/{quote(pathname, safe="/")}'

from app.main import app  # noqa: E402,F401
