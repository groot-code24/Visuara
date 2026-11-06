'use client';

import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api, ApiError } from '@/lib/api';
import type { Invitation, Member, Usage } from '@/lib/types';
import { Alert, Badge, Button, Card, Input, Meter, PageHeader, Select, Skeleton } from '@/components/ui';
import { useToast } from '@/components/toast';
import { ROLE_HELP, bytes, when } from '@/lib/format';
import { useMe, can } from '../shell';
import { BillingPanel } from './billing';

type Members = { organization: { id: number; name: string }; you: { id: number; member_role: string }; roles: string[]; members: Member[] };

export default function Settings() {
  const me = useMe();
  const toast = useToast();
  const usage = useApi<Usage>('/api/usage');
  const members = useApi<Members>('/api/members');
  const invites = useApi<Invitation[]>(can(me, 'admin') ? '/api/invitations' : null);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('editor');
  const [busy, setBusy] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const admin = can(me, 'admin');

  async function run(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); toast(ok, 'good'); } catch (e) { toast(e instanceof ApiError ? e.message : 'Action failed', 'bad'); }
    members.reload(); invites.reload(); usage.reload();
  }

  async function invite(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setInviteError(null);
    try {
      const r = await api<{ emailed: boolean }>('/api/invitations', { json: { email, member_role: role } });
      toast(r.emailed ? `Invitation sent to ${email}` : `Invitation created. Ask ${email} to sign in with this address.`, 'good');
      setEmail(''); invites.reload();
    } catch (err) { setInviteError(err instanceof ApiError ? err.message : 'Could not invite'); }
    finally { setBusy(false); }
  }

  const u = usage.data;
  return (
    <>
      <PageHeader title="Settings" description={`${me.organization} · signed in as ${me.email}`} />
      <div className="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
        <Card id="plan" className="p-6">
          <div className="flex items-start justify-between">
            <div><p className="text-sm text-muted">Plan</p><p className="font-display text-3xl">{u?.plan_limits.name ?? '…'}</p></div>
            {u && <Badge tone="accent">{u.plan_limits.price_usd_month == null ? 'Custom' : u.plan_limits.price_usd_month === 0 ? 'Free' : `$${u.plan_limits.price_usd_month}/month`}</Badge>}
          </div>
          {u ? (
            <div className="mt-6 space-y-4">
              <Meter label="Decisions this month" used={u.usage.decisions_this_month} limit={u.plan_limits.decisions_per_month} />
              <Meter label="Rendered videos this month" used={u.usage.renders_this_month} limit={u.plan_limits.renders_per_month} />
              <Meter label="Storage" used={u.usage.storage_bytes} limit={u.effective.storage_mb * 1048576} format={bytes} />
              <Meter label="Members" used={u.usage.members} limit={u.effective.members} />
              <p className="text-xs text-muted">Videos up to {u.plan_limits.max_video_seconds ? `${u.plan_limits.max_video_seconds} s` : 'the platform limit'} · up to {u.plan_limits.max_videos_per_decision} videos per decision.</p>
            </div>
          ) : <Skeleton className="mt-6 h-40" />}
          <BillingPanel organization={me.organization || ''} onChange={usage.reload} />
        </Card>

        <Card className="p-6">
          <div className="flex items-center justify-between"><h2 className="font-semibold">Team</h2><span className="text-sm text-muted">Your role: {me.member_role}</span></div>
          {admin && (
            <form onSubmit={invite} className="mt-4 grid gap-2 sm:grid-cols-[1fr_140px_auto]">
              <Input type="email" required placeholder="colleague@company.com" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Email to invite" />
              <Select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
                {(members.data?.roles || ['viewer', 'editor', 'manager', 'admin']).filter((r) => r !== 'admin' || me.member_role === 'owner').map((r) => <option key={r} value={r}>{r}</option>)}
              </Select>
              <Button type="submit" loading={busy}>Invite</Button>
            </form>
          )}
          {admin && <p className="mt-2 text-xs text-muted">{ROLE_HELP[role]}</p>}
          {inviteError && <Alert className="mt-3">{inviteError}</Alert>}

          {!members.data ? <Skeleton className="mt-5 h-32" /> : (
            <ul className="mt-5 divide-y divide-line">
              {members.data.members.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{m.name}{m.id === me.id && <span className="text-muted"> (you)</span>}</p>
                    <p className="truncate text-xs text-muted">{m.email}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {m.suspended && <Badge tone="bad">Suspended</Badge>}
                    {admin && m.member_role !== 'owner' && m.id !== me.id ? (
                      <>
                        <Select className="h-8 w-28 text-sm" value={m.member_role} aria-label={`Role of ${m.email}`}
                          onChange={(e) => run(() => api(`/api/members/${m.id}`, { method: 'PATCH', json: { member_role: e.target.value } }), 'Role updated')}>
                          {['viewer', 'editor', 'manager', 'admin'].map((r) => <option key={r} value={r}>{r}</option>)}
                        </Select>
                        <Button size="sm" variant="ghost" onClick={() => run(() => api(`/api/members/${m.id}/${m.suspended ? 'reactivate' : 'suspend'}`, { method: 'POST' }), m.suspended ? 'Reactivated' : 'Suspended')}>
                          {m.suspended ? 'Reactivate' : 'Suspend'}
                        </Button>
                        <Button size="sm" variant="ghost" className="text-bad" onClick={() => confirm(`Remove ${m.email} from the workspace?`) && run(() => api(`/api/members/${m.id}`, { method: 'DELETE' }), 'Member removed')}>Remove</Button>
                      </>
                    ) : <Badge>{m.member_role}</Badge>}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {admin && invites.data && invites.data.some((i) => i.status === 'pending') && (
            <div className="mt-5 border-t border-line pt-4">
              <p className="mb-2 text-sm font-medium">Pending invitations</p>
              <ul className="space-y-2">
                {invites.data.filter((i) => i.status === 'pending').map((i) => (
                  <li key={i.id} className="flex items-center justify-between text-sm">
                    <span className="truncate">{i.email} <span className="text-muted">· {i.member_role} · {when(i.created_at)}</span></span>
                    <Button size="sm" variant="ghost" onClick={() => run(() => api(`/api/invitations/${i.id}`, { method: 'DELETE' }), 'Invitation revoked')}>Revoke</Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
