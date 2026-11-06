'use client';

import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import type { Org } from '@/lib/types';
import { Badge, Button, Card, Input, Label, PageHeader, Select, Textarea } from '@/components/ui';
import { useToast } from '@/components/toast';
import { useMe, can } from '../shell';

type Invitation = { id: number; organization: string; kind: string; member_role: string };
type Grant = { id: number; agency_org_id: number; agency: string; role: string; status: string };
type Client = { id: number; client_org_id: number; client: string; role: string };
type Profile = { kind: string; profile: { display_name: string; website: string; description: string } | null };

export default function Organizations() {
  const me = useMe(); const toast = useToast();
  const orgs = useApi<{ organizations: Org[] }>('/api/organizations');
  const invites = useApi<Invitation[]>('/api/invitations/mine');
  const profile = useApi<Profile>(me.org_kind === 'direct' ? null : '/api/profile');
  const [name, setName] = useState(''); const [kind, setKind] = useState('brand');
  const admin = can(me, 'admin') && !orgs.data?.organizations.find((o) => o.active)?.via_agency;
  const grants = useApi<Grant[]>(admin && (me.org_kind === 'brand' || me.org_kind === 'direct') ? '/api/agency/grants' : null);
  const clients = useApi<Client[]>(me.org_kind === 'agency' ? '/api/agency/clients' : null);
  const [agencyId, setAgencyId] = useState(''); const [grantRole, setGrantRole] = useState('manager');
  async function go(path: string, json: unknown, ok: string, method = 'POST', reload = false) {
    try { await api(path, { method, json }); toast(ok, 'good'); if (reload) window.location.assign('/dashboard/organizations'); else { orgs.reload(); invites.reload(); profile.reload(); grants.reload(); clients.reload(); } }
    catch (e) { toast(e instanceof Error ? e.message : 'Action failed', 'bad'); }
  }
  async function saveProfile(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); const f = new FormData(e.currentTarget);
    go('/api/profile', { display_name: f.get('display_name'), website: f.get('website'), description: f.get('description') }, 'Profile saved', 'PUT');
  }
  return (
    <>
      <PageHeader title="Organizations" description="You can belong to several organizations. Publishers sell scenes, brands and agencies buy them, direct workspaces place their own products." />
      <Card className="mb-6"><ul className="divide-y divide-line">
        {orgs.data?.organizations.map((o) => (
          <li key={o.id} className="flex items-center justify-between gap-3 px-5 py-3 text-sm">
            <span>{o.name} <Badge>{o.kind}</Badge> <span className="text-muted">· {o.member_role}{o.status === 'DELEGATED' ? ' · through your agency' : o.status !== 'ACTIVE' && ` · ${o.status.toLowerCase()}`}</span></span>
            {o.active ? <Badge tone="good">active</Badge> : (o.status === 'ACTIVE' || o.status === 'DELEGATED') && <Button size="sm" variant="secondary" onClick={() => go('/api/organizations/switch', { org_id: o.id }, 'Switched', 'POST', true)}>Switch</Button>}
          </li>
        ))}
      </ul></Card>
      {!!invites.data?.length && (
        <Card className="mb-6 p-5"><h2 className="mb-2 font-semibold">Invitations</h2>
          {invites.data.map((i) => <p key={i.id} className="flex items-center justify-between text-sm">{i.organization} ({i.kind}) as {i.member_role}
            <Button size="sm" onClick={() => go(`/api/invitations/${i.id}/accept`, {}, 'Joined')}>Accept</Button></p>)}
        </Card>
      )}
      <Card className="mb-6 p-5">
        <h2 className="mb-3 font-semibold">New organization</h2>
        <form className="grid gap-3 sm:grid-cols-[1fr_180px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); go('/api/organizations', { name, kind }, 'Organization created'); setName(''); }}>
          <div><Label htmlFor="on">Name</Label><Input id="on" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /></div>
          <div><Label htmlFor="ok">Kind</Label><Select id="ok" value={kind} onChange={(e) => setKind(e.target.value)}>{['brand', 'publisher', 'agency', 'direct'].map((k) => <option key={k}>{k}</option>)}</Select></div>
          <Button type="submit">Create</Button>
        </form>
      </Card>
      {me.member_role === 'owner' && (
        <Card className="mb-6 p-5">
          <h2 className="mb-3 font-semibold">This organization&apos;s kind</h2>
          <div className="flex flex-wrap gap-2">{['direct', 'brand', 'publisher', 'agency'].map((k) => (
            <Button key={k} size="sm" variant={k === me.org_kind ? 'primary' : 'secondary'} onClick={() => go('/api/organizations/current', { kind: k }, `Now a ${k} organization`, 'PATCH', true)}>{k}</Button>
          ))}</div>
        </Card>
      )}
      {me.org_kind === 'agency' && (
        <Card className="mb-6 p-5">
          <h2 className="mb-1 font-semibold">Clients</h2>
          <p className="mb-3 text-sm text-muted">Your agency id is <span className="font-medium text-ink">{me.org_id}</span>. A brand gives your agency access with it; you then switch into the brand above and work there within the role it gave.</p>
          {!clients.data?.length ? <p className="text-sm text-muted">No client has given access yet.</p> : (
            <ul className="divide-y divide-line text-sm">{clients.data.map((cl) => (
              <li key={cl.id} className="flex items-center justify-between py-2"><span>{cl.client} <span className="text-muted">· {cl.role}</span></span>
                {can(me, 'admin') && <Button size="sm" variant="ghost" onClick={() => go(`/api/agency/clients/${cl.id}/leave`, {}, 'Access given up')}>Give up access</Button>}</li>
            ))}</ul>
          )}
        </Card>
      )}
      {grants.data && (
        <Card className="mb-6 p-5">
          <h2 className="mb-1 font-semibold">Agency access</h2>
          <p className="mb-3 text-sm text-muted">Let an agency work in this organization. Its people act within the role you give, never as owner or admin, and lose access as soon as you revoke it.</p>
          <form className="mb-3 grid gap-3 sm:grid-cols-[1fr_160px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); go('/api/agency/grants', { agency_org_id: Number(agencyId), role: grantRole }, 'Access given'); setAgencyId(''); }}>
            <div><Label htmlFor="ag">Agency id</Label><Input id="ag" inputMode="numeric" value={agencyId} onChange={(e) => setAgencyId(e.target.value)} required pattern="[0-9]+" /></div>
            <div><Label htmlFor="ar">Role</Label><Select id="ar" value={grantRole} onChange={(e) => setGrantRole(e.target.value)}>{['viewer', 'editor', 'manager'].map((r) => <option key={r}>{r}</option>)}</Select></div>
            <Button type="submit">Give access</Button>
          </form>
          {grants.data.length > 0 && <ul className="divide-y divide-line text-sm">{grants.data.map((g) => (
            <li key={g.id} className="flex items-center justify-between py-2"><span>{g.agency} <span className="text-muted">· {g.role} · {g.status.toLowerCase()}</span></span>
              {g.status === 'ACTIVE' && <Button size="sm" variant="ghost" onClick={() => go(`/api/agency/grants/${g.id}/revoke`, {}, 'Access revoked')}>Revoke</Button>}</li>
          ))}</ul>}
        </Card>
      )}
      {profile.data && can(me, 'manager') && (
        <Card className="p-5">
          <h2 className="mb-3 font-semibold">Marketplace profile</h2>
          <form onSubmit={saveProfile} className="grid gap-3">
            <div><Label htmlFor="dn">Display name</Label><Input id="dn" name="display_name" defaultValue={profile.data.profile?.display_name || me.organization || ''} required minLength={2} /></div>
            <div><Label htmlFor="ws" hint="https://">Website</Label><Input id="ws" name="website" defaultValue={profile.data.profile?.website || ''} /></div>
            <div><Label htmlFor="ds">Description</Label><Textarea id="ds" name="description" defaultValue={profile.data.profile?.description || ''} /></div>
            <Button type="submit">Save profile</Button>
          </form>
        </Card>
      )}
    </>
  );
}
