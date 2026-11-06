"""Grant or revoke platform administration. There is deliberately no API for this.

    python -m ops.platform_admin grant person@example.com
    python -m ops.platform_admin revoke person@example.com
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))


def set_flag(email, value):
    from app import main
    with main.db() as c:
        n = c.execute('UPDATE users SET platform_admin=? WHERE lower(email)=lower(?) AND removed_at IS NULL', (1 if value else 0, email)).rowcount
        if n: c.execute("INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) SELECT org_id,id,?,'user',id,'{}' FROM users WHERE lower(email)=lower(?)",
                        ('platform_admin.granted' if value else 'platform_admin.revoked', email))
    return n


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('grant', 'revoke'): raise SystemExit(__doc__)
    n = set_flag(sys.argv[2], sys.argv[1] == 'grant')
    print(f'{sys.argv[1]}: {"done" if n else "no such active account"}')
