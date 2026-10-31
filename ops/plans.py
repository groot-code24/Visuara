"""Workspace plans (billing is by invoice during the pilot, so plan changes are an operator action).

    python -m ops.plans list
    python -m ops.plans set --org "Acme" --plan pro       # free | pro | team | enterprise
"""
import argparse, json, sys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    s = sub.add_parser('set'); s.add_argument('--org', required=True, help='workspace name or id'); s.add_argument('--plan', required=True)
    a = ap.parse_args(argv)
    from app import main as m, limits
    with m.db() as c:
        if a.cmd == 'list':
            for r in c.execute('SELECT id,name,plan FROM organizations ORDER BY id').fetchall():
                key = limits.org_plan(c, r['id']); used = limits.org_usage(c, r['id'])
                print(json.dumps({'id': r['id'], 'name': r['name'], 'plan': key, 'decisions_this_month': used['decisions_this_month'],
                                  'renders_this_month': used['renders_this_month'], 'members': used['members']}))
            return
        if a.plan not in limits.PLANS: sys.exit(f'unknown plan {a.plan!r}; choose one of {", ".join(limits.PLANS)}')
        org = c.execute('SELECT id,name FROM organizations WHERE id=? OR lower(name)=lower(?)', (int(a.org) if a.org.isdigit() else -1, a.org)).fetchone()
        if not org: sys.exit(f'no workspace {a.org!r}')
        c.execute('UPDATE organizations SET plan=? WHERE id=?', (a.plan, org['id']))
        c.execute("INSERT INTO events(org_id,user_id,event_type,entity_type,entity_id,details) VALUES(?,0,'plan.changed','organization',?,?)",
                  (org['id'], org['id'], json.dumps({'plan': a.plan, 'by': 'operator'})))
        print(f'{org["name"]} (id {org["id"]}) is now on {limits.PLANS[a.plan]["name"]}')


if __name__ == '__main__':
    main()
