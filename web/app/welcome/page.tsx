'use client';

import { useEffect, useState } from 'react';
import { AuthShell } from '@/components/auth-shell';
import { Alert, Button, Input, Label, Spinner, cx } from '@/components/ui';
import { api, ApiError } from '@/lib/api';

type Kind = 'direct' | 'brand' | 'publisher' | 'agency';
const KINDS: { id: Kind; title: string; detail: string }[] = [
  { id: 'brand', title: 'A brand', detail: 'Find scenes in publishers’ videos and place your products in them' },
  { id: 'publisher', title: 'A publisher or studio', detail: 'Analyse your videos and offer their scenes to brands' },
  { id: 'agency', title: 'An agency', detail: 'Run placement campaigns for brands you represent' },
  { id: 'direct', title: 'Just my own videos', detail: 'Place your own products in your own videos' },
];

type Pending = { email: string; name: string; provider: string; suggested_workspace: string; invitations: { id: number; member_role: string; organization: string }[] };

export default function WelcomePage() {
  const [p, setP] = useState<Pending | null>(null);
  const [name, setName] = useState('');
  const [org, setOrg] = useState('');
  const [choice, setChoice] = useState<number | 'new'>('new');
  const [kind, setKind] = useState<Kind>('direct');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Pending>('/api/auth/signup', { redirectOn401: false })
      .then((d) => {
        setP(d); setName(d.name); setOrg(d.suggested_workspace);
        if (d.invitations.length) setChoice(d.invitations[0].id);
      })
      .catch(() => window.location.replace('/dashboard'));   // no sign-up in progress: signed in already, or back to /login via the guard
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      await api('/api/auth/signup', { json: choice === 'new' ? { name, organization: org, kind } : { name, invitation_id: choice } });
      window.location.assign('/dashboard');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not finish setting up your account.'); setBusy(false);
    }
  }

  async function cancel() {
    await api('/api/auth/signup/cancel', { method: 'POST' }).catch(() => {});
    window.location.assign('/login');
  }

  if (!p) return <AuthShell><div className="flex justify-center py-16 text-muted"><Spinner size={24} /></div></AuthShell>;

  return (
    <AuthShell>
      <h1 className="text-center font-display text-[40px] leading-tight tracking-tight">Let&apos;s set you up</h1>
      <p className="mt-2 text-center text-[15px] text-muted">Signing up as <span className="font-medium text-ink">{p.email}</span></p>
      <form onSubmit={submit} className="mt-8 rounded-2xl border border-line bg-card p-6 shadow-card">
        {error && <Alert className="mb-4">{error}</Alert>}
        <Label htmlFor="name">Your name</Label>
        <Input id="name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="Alex Morgan" />

        {p.invitations.length > 0 && (
          <div className="mt-5">
            <Label>Workspace</Label>
            <div className="space-y-2">
              {p.invitations.map((i) => (
                <button type="button" key={i.id} onClick={() => setChoice(i.id)}
                  className={cx('w-full rounded-xl border px-4 py-3 text-left transition', choice === i.id ? 'border-ink bg-soft' : 'border-line-2 hover:bg-soft')}>
                  <span className="block font-medium">Join {i.organization}</span>
                  <span className="text-sm text-muted">You were invited as {i.member_role}</span>
                </button>
              ))}
              <button type="button" onClick={() => setChoice('new')}
                className={cx('w-full rounded-xl border px-4 py-3 text-left transition', choice === 'new' ? 'border-ink bg-soft' : 'border-line-2 hover:bg-soft')}>
                <span className="block font-medium">Create a new workspace</span>
              </button>
            </div>
          </div>
        )}

        {choice === 'new' && (
          <div className="mt-5">
            <Label htmlFor="org" hint="Your company, brand or studio">Workspace name</Label>
            <Input id="org" required maxLength={120} value={org} onChange={(e) => setOrg(e.target.value)} />
            <fieldset className="mt-5">
              <legend className="mb-2 text-sm font-medium">This workspace is</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {KINDS.map((k) => (
                  <label key={k.id} className={cx('cursor-pointer rounded-xl border px-4 py-3 transition', kind === k.id ? 'border-ink bg-soft' : 'border-line-2 hover:bg-soft')}>
                    <input type="radio" name="kind" value={k.id} checked={kind === k.id} onChange={() => setKind(k.id)} className="sr-only" />
                    <span className="block font-medium">{k.title}</span>
                    <span className="text-sm text-muted">{k.detail}</span>
                  </label>
                ))}
              </div>
              <p className="mt-2 text-xs text-muted">The owner can change this later in Organizations, until the workspace has marketplace activity.</p>
            </fieldset>
          </div>
        )}
        <Button type="submit" size="lg" className="mt-6 w-full" loading={busy} disabled={!name.trim() || (choice === 'new' && !org.trim())}>
          {choice === 'new' ? 'Create workspace' : 'Join workspace'}
        </Button>
      </form>
      <button onClick={cancel} className="mx-auto mt-5 block text-sm text-muted hover:text-ink">Cancel and sign out</button>
    </AuthShell>
  );
}
