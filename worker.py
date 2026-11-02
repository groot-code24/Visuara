"""Standalone worker process. Run with the same CC_DATA_DIR as the API."""
import signal
from app import main

def stop(*_):
    main._WORKER_STOP.set()

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
main._WORKER_STOP.clear()
print('Visora worker started; waiting for SQLite jobs...', flush=True)
main._worker_loop()
print('Visora worker stopped.', flush=True)
