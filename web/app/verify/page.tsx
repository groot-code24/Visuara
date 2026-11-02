'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { AuthShell } from '@/components/auth-shell';
import { Alert, Button, buttonClass, Spinner } from '@/components/ui';
import { api, ApiError } from '@/lib/api';

/**
 * Magic-link landing page. The token arrives in the URL fragment (never sent to any server or logged). Mail
 * scanners open links automatically, so signing in needs an explicit click.
 */
export default function VerifyPage() {
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [state, setState] = useState<'checking' | 'ready' | 'expired' | 'signing'>('checking');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = new URLSearchParams(window.location.hash.slice(1)).get('token');
    history.replaceState(null, '', '/verify');                   // drop the token from the address bar and history
    if (!t) { setState('expired'); return; }
    setToken(t);
    api<{ email: string }>('/api/auth/email/link/check', { json: { token: t } })
      .then((r) => { setEmail(r.email); setState('ready'); })
      .catch(() => setState('expired'));
  }, []);

  async function confirm() {
    if (!token) return;
    setState('signing'); setError(null);
    try {
      const r = await api<{ status: string }>('/api/auth/email/link', { json: { token } });
      window.location.assign(r.status === 'signed_in' ? '/dashboard' : '/welcome');
    } catch (err) {
      if (err instanceof ApiError && err.code === 'LINK_EXPIRED') setState('expired');
      else { setError(err instanceof ApiError ? err.message : 'Sign-in failed.'); setState('ready'); }
    }
  }

  return (
    <AuthShell>
      {state === 'checking' && <div className="flex justify-center py-16 text-muted"><Spinner size={24} /></div>}
      {(state === 'ready' || state === 'signing') && (
        <div className="text-center">
          <h1 className="font-display text-[40px] leading-tight tracking-tight">Sign in to Visora</h1>
          <p className="mt-2 text-[15px] text-muted">Continue as <span className="font-medium text-ink">{email}</span></p>
          <div className="mt-8 rounded-2xl border border-line bg-card p-6 text-left shadow-card">
            {error && <Alert className="mb-4">{error}</Alert>}
            <Button size="lg" className="w-full" onClick={confirm} loading={state === 'signing'}>Continue</Button>
            <p className="mt-4 text-center text-xs text-muted">Not you? <Link className="underline" href="/login">Use another account</Link></p>
          </div>
        </div>
      )}
      {state === 'expired' && (
        <div className="text-center">
          <h1 className="font-display text-[40px] leading-tight tracking-tight">This link has expired</h1>
          <p className="mt-2 text-[15px] text-muted">Sign-in links work once and expire after 10 minutes.</p>
          <Link href="/login" className={buttonClass('primary', 'lg', 'mt-8 w-full')}>Request a new link</Link>
        </div>
      )}
    </AuthShell>
  );
}
