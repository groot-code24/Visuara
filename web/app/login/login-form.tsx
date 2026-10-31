'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { api, ApiError } from '@/lib/api';
import { Alert, Button, Input, Label, buttonClass, cx } from '@/components/ui';
import { GoogleLogo, IconMail } from '@/components/icons';

const ERRORS: Record<string, string> = {
  google_failed: 'Google sign-in could not be completed. Please try again, or continue with email.',
  google_cancelled: 'Google sign-in was cancelled.',
  google_unavailable: 'Google sign-in is not available right now. Continue with email instead.',
  suspended: 'This account is suspended. Contact your workspace owner.',
  expired: 'That sign-in link has expired or was already used. Request a new one.',
};

type Providers = { google: boolean; email: boolean; email_backend: string | null; development?: boolean };
type DevLink = { link: string; code: string };
const DEV_BUILD = process.env.NODE_ENV === 'development';

export function LoginForm({ next, error, invited, plan }: { next: string; error?: string; invited?: boolean; plan?: string }) {
  const [providers, setProviders] = useState<Providers | null>(null);
  const [step, setStep] = useState<'start' | 'sent'>('start');
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [down, setDown] = useState(false);
  const [dev, setDev] = useState<DevLink | null>(null);
  const [message, setMessage] = useState<string | null>(error ? ERRORS[error] || 'Sign-in failed. Please try again.' : null);

  useEffect(() => {
    api<Providers>('/api/auth/providers').then(setProviders).catch(() => {
      setProviders({ google: false, email: false, email_backend: null }); setDown(true);
      setMessage(DEV_BUILD
        ? 'The API is not running, so nobody can sign in. Start it in a second terminal: .\\scripts\\dev.ps1 run'
        : 'We can\u2019t reach the sign-in service right now. Please try again in a few minutes.');
    });
  }, []);

  async function start(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true); setMessage(null);
    try {
      const r = await api<{ email: string; dev?: DevLink }>('/api/auth/email/start', { json: { email } });
      setEmail(r.email); setDev(r.dev ?? null); setStep('sent');
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not send the email.');
    } finally { setBusy(false); }
  }

  if (step === 'sent') {
    return <CheckEmail email={email} next={next} dev={dev} onBack={() => { setStep('start'); setMessage(null); }} onResend={start} />;
  }

  return (
    <div>
      <h1 className="text-center font-display text-[40px] leading-tight tracking-tight">
        {invited ? 'Join your team' : 'Welcome'}
      </h1>
      <p className="mt-2 text-center text-[15px] text-muted">
        {invited ? 'Sign in with the email address your invitation was sent to.' : plan && plan !== 'free' ? 'Create your workspace on the Free plan, then ask us to switch it. Paid plans are set up by our team during the pilot.' : 'Sign in or create your account'}
      </p>

      <div className="mt-8 rounded-2xl border border-line bg-card p-6 shadow-card">
        {message && <Alert className="mb-4">{message}</Alert>}
        {providers?.google !== false ? (
          <a href="/auth/google/start" aria-disabled={!providers}
            className={buttonClass('secondary', 'lg', cx('w-full gap-3', !providers && 'pointer-events-none opacity-60'))}>
            <GoogleLogo /> Continue with Google
          </a>
        ) : providers.development && !down ? (
          <div>
            <button type="button" disabled className={buttonClass('secondary', 'lg', 'w-full gap-3 opacity-60')}><GoogleLogo /> Continue with Google</button>
            <p className="mt-2 rounded-lg bg-warn-soft px-3 py-2 text-xs text-warn">
              Development: Google sign-in needs your own OAuth client. Add <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> to <code>.env</code>, then restart the API.
            </p>
          </div>
        ) : null}
        {(providers?.google !== false || (providers.development && !down)) && (
          <div className="my-5 flex items-center gap-3 text-xs uppercase tracking-wider text-muted">
            <span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" />
          </div>
        )}
        <form onSubmit={start}>
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" required placeholder="you@company.com" value={email}
            onChange={(e) => setEmail(e.target.value)} disabled={providers?.email === false} autoFocus />
          <Button type="submit" size="lg" className="mt-3 w-full" loading={busy} disabled={!email || providers?.email === false}>
            Continue with email
          </Button>
          {providers?.email === false && !down && <p className="mt-3 text-center text-xs text-muted">Email sign-in is not configured on this server.</p>}
        </form>
      </div>
      <p className="mt-6 text-center text-xs leading-relaxed text-muted">
        We&apos;ll email you a magic link and a one-time code. No password needed.<br />
        By continuing, you agree to our <Link href="/terms" className="underline">Terms</Link> and <Link href="/privacy" className="underline">Privacy Policy</Link>.
      </p>
    </div>
  );
}

function CheckEmail({ email, next, dev, onBack, onResend }: { email: string; next: string; dev: DevLink | null; onBack: () => void; onResend: () => Promise<void> }) {
  const [digits, setDigits] = useState<string[]>(Array(6).fill(''));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(30);
  const refs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => { refs.current[0]?.focus(); }, []);
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  async function verify(code: string) {
    setBusy(true); setError(null);
    try {
      const r = await api<{ status: string }>('/api/auth/email/verify', { json: { email, code } });
      window.location.assign(r.status === 'signed_in' ? next : '/welcome');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That code did not work.');
      setDigits(Array(6).fill('')); refs.current[0]?.focus(); setBusy(false);
    }
  }

  function set(i: number, v: string) {
    const clean = v.replace(/\D/g, '');
    if (clean.length > 1) {                       // pasted the whole code
      const all = clean.slice(0, 6).split('');
      const nextDigits = Array(6).fill('').map((_, k) => all[k] || '');
      setDigits(nextDigits);
      if (all.length === 6) verify(all.join('')); else refs.current[all.length]?.focus();
      return;
    }
    const nextDigits = [...digits]; nextDigits[i] = clean;
    setDigits(nextDigits);
    if (clean && i < 5) refs.current[i + 1]?.focus();
    if (nextDigits.every(Boolean)) verify(nextDigits.join(''));
  }

  return (
    <div>
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-card text-ink shadow-card"><IconMail size={24} /></div>
      <h1 className="mt-6 text-center font-display text-[40px] leading-tight tracking-tight">Check your email</h1>
      <p className="mt-2 text-center text-[15px] text-muted">
        We sent a sign-in link to <span className="font-medium text-ink">{email}</span>. Click it, or enter the 6-digit code below.
      </p>
      <div className="mt-8 rounded-2xl border border-line bg-card p-6 shadow-card">
        {error && <Alert className="mb-4">{error}</Alert>}
        <div className="flex justify-between gap-2" role="group" aria-label="6-digit sign-in code">
          {digits.map((d, i) => (
            <input key={i} ref={(el) => { refs.current[i] = el; }} value={d} inputMode="numeric" autoComplete={i === 0 ? 'one-time-code' : 'off'}
              aria-label={`Digit ${i + 1}`} maxLength={i === 0 ? 6 : 1} disabled={busy}
              onChange={(e) => set(i, e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Backspace' && !digits[i] && i > 0) refs.current[i - 1]?.focus(); }}
              className="h-14 w-12 rounded-xl border border-line-2 bg-card text-center text-2xl font-semibold tabular-nums outline-none focus:border-ink/40 focus:ring-4 focus:ring-ink/5 sm:w-[52px]" />
          ))}
        </div>
        <Button size="lg" className="mt-5 w-full" loading={busy} disabled={!digits.every(Boolean)} onClick={() => verify(digits.join(''))}>Verify code</Button>
        <div className="mt-4 flex items-center justify-between text-sm">
          <button className="text-muted hover:text-ink" onClick={onBack}>Use a different email</button>
          <button className="font-medium text-ink disabled:text-muted" disabled={cooldown > 0}
            onClick={async () => { await onResend(); setCooldown(30); }}>
            {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend email'}
          </button>
        </div>
      </div>
      {dev && (
        <div className="mt-4 rounded-xl border border-warn/30 bg-warn-soft p-4 text-center text-sm text-warn">
          <p className="font-medium">Development: no email provider is set up, so nothing was sent.</p>
          <a href={dev.link} className={buttonClass('secondary', 'md', 'mt-3 w-full')}>Open the magic link</a>
          <p className="mt-3 text-xs">Or enter this code: <code className="text-sm font-semibold tracking-widest">{dev.code}</code></p>
        </div>
      )}
    </div>
  );
}
