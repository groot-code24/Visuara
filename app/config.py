"""Load KEY=VALUE lines from a .env file into os.environ. Real environment variables always win.

The file is the repository-root .env, or CC_ENV_FILE. Set CC_DOTENV=0 to disable (the test suite does, so tests
never touch the database or services configured for a real deployment). Values may be quoted; in unquoted values
a ' #' starts a comment.
"""
import os
from pathlib import Path


def load_dotenv(path=None):
    if os.getenv('CC_DOTENV', '1') == '0': return []
    p = Path(path or os.getenv('CC_ENV_FILE') or Path(__file__).resolve().parent.parent / '.env')
    if not p.is_file(): return []
    loaded = []
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        if line.startswith('export '): line = line[7:]
        key, value = (x.strip() for x in line.split('=', 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'': value = value[1:-1]
        elif ' #' in value: value = value.split(' #', 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value; loaded.append(key)
    return loaded
