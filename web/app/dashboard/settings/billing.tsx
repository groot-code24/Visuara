'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api, ApiError } from '@/lib/api';
import { Alert, Badge, Button, cx } from '@/components/ui';
import { IconShield } from '@/components/icons';
import { useToast } from '@/components/toast';
import { contactHref } from '@/lib/site';

type Interval = 'monthly' | 'annual';
type BillingPlan = { id: 'pro' | 'team'; name: string; monthly: number; annual: number; available: Record<Interval, boolean> };
export type Billing = {
  plan: string; plan_name: string; provider: string | null; ready: boolean; problem: string | null; mode: 'test' | 'live' | null; can_manage: boolean;
  plans: BillingPlan[];
  subscription: { plan: string; billing_interval: Interval | null; status: string; renews_at: string | null; ends_at: string | null; test_mode: number; card_brand: string | null; card_last_four: string | null } | null;
};

const STATUS: Record<string, [string, 'good' | 'warn' | 'bad' | 'neutral']> = {
  active: ['Active', 'good'], on_trial: ['Trial', 'good'], past_due: ['Payment retrying', 'warn'], unpaid: ['Unpaid', 'bad'],
  cancelled: ['Cancelled', 'warn'], expired: ['Expired', 'neutral'], paused: ['Paused', 'neutral'],
};
const LIVE = ['on_trial', 'active', 'past_due', 'cancelled'];
const day = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

/** The plan card's billing section: buy, change or manage the workspace's subscription through Lemon Squeezy. */
export function BillingPanel({ organization, onChange }: { organization: string; onChange: () => void }) {
  const toast = useToast();
  // Back from the checkout (?billing=success): Lemon Squeezy confirms by webhook a few seconds later.
  const [returned] = useState(() => { try { return new URLSearchParams(window.location.search).get('billing') === 'success'; } catch { return false; } });
  const started = useRef(0);
  const b = useApi<Billing>('/api/billing', {
    // so keep looking for up to a minute.
    poll: (d) => returned && Date.now() - started.current < 60000 && !(d.subscription && LIVE.includes(d.subscription.status)), interval: 2500,
  });
  const [periodChoice, setPeriod] = useState<Interval | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastPlan = useRef<string | null>(null);

  useEffect(() => {
    started.current = Date.now();
    if (returned) void b.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!b.data) return;
    if (lastPlan.current && lastPlan.current !== b.data.plan) { toast(`Your workspace is now on ${b.data.plan_name}`, 'good'); onChange(); }
    lastPlan.current = b.data.plan;
  }, [b.data, onChange, toast]);

  const d = b.data;
  if (!d) return null;
  const period: Interval = periodChoice ?? d.subscription?.billing_interval ?? 'annual';
  const sub = d.subscription && LIVE.includes(d.subscription.status) ? d.subscription : null;

  async function go(kind: 'checkout' | 'change' | 'portal', plan?: string) {
    setBusy(kind + (plan || '')); setError(null);
    try {
      if (kind === 'portal') { const r = await api<{ portal_url: string }>('/api/billing/portal', { method: 'POST' }); window.location.assign(r.portal_url); return; }
      if (kind === 'checkout') { const r = await api<{ checkout_url: string }>('/api/billing/checkout', { json: { plan, interval: period } }); window.location.assign(r.checkout_url); return; }
      await api('/api/billing/change', { json: { plan, interval: period } });
      toast('Plan changed. Lemon Squeezy prorates the difference on your next invoice.', 'good'); b.reload(); onChange();
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Something went wrong. Please try again.'); }
    setBusy(null);
  }

  if (!d.ready) {
    return (
      <div className="mt-6 rounded-xl bg-soft p-4 text-sm text-ink-2">
        Online payment is not switched on yet.{' '}
        <a className="font-medium text-ink underline" href={contactHref(`Upgrade ${organization}`, '/pricing')}>Contact us to upgrade</a>
        {' '}· <Link href="/pricing" className="underline">Compare plans</Link>
        {d.problem && d.can_manage && <p className="mt-2 text-xs text-muted">Setup: {d.problem}</p>}
      </div>
    );
  }

  return (
    <div className="mt-6 border-t border-line pt-5">
      {returned && !sub && <Alert tone="good" className="mb-4">Thanks! We are waiting for Lemon Squeezy to confirm the payment. This page updates by itself.</Alert>}
      {error && <Alert className="mb-4">{error}</Alert>}
      {sub ? (
        <div className="rounded-xl border border-line p-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium">{d.plans.find((x) => x.id === sub.plan)?.name ?? sub.plan} · {sub.billing_interval === 'annual' ? 'billed yearly' : 'billed monthly'}</p>
            <div className="flex gap-1.5">
              {sub.test_mode ? <Badge tone="warn">Test mode</Badge> : null}
              <Badge tone={(STATUS[sub.status] || [sub.status, 'neutral'])[1]}>{(STATUS[sub.status] || [sub.status])[0]}</Badge>
            </div>
          </div>
          <p className="mt-1 text-muted">
            {sub.status === 'cancelled' && sub.ends_at ? `Ends ${day(sub.ends_at)}; you keep the plan until then.` : sub.renews_at ? `Renews ${day(sub.renews_at)}` : null}
            {sub.card_last_four && ` · ${sub.card_brand ?? 'Card'} ending ${sub.card_last_four}`}
          </p>
          {sub.status === 'past_due' && <p className="mt-2 text-warn">The last payment failed. Update your card to keep the plan.</p>}
          {d.can_manage && (
            <Button size="sm" variant="secondary" className="mt-3" loading={busy === 'portal'} onClick={() => go('portal')}>
              Manage billing, card and invoices
            </Button>
          )}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{sub ? 'Switch plan' : 'Upgrade'}</p>
        <div className="inline-flex rounded-lg border border-line bg-canvas p-0.5 text-xs" role="group" aria-label="Billing period">
          {(['monthly', 'annual'] as const).map((v) => (
            <button key={v} type="button" onClick={() => setPeriod(v)} aria-pressed={period === v}
              className={cx('rounded-md px-3 py-1 font-medium transition', period === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-soft')}>
              {v === 'monthly' ? 'Monthly' : 'Annual −25%'}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {d.plans.map((p) => {
          const current = sub && sub.plan === p.id && sub.billing_interval === period;
          const ok = p.available[period];
          const price = period === 'annual' ? p.annual : p.monthly;
          return (
            <div key={p.id} className={cx('rounded-xl border p-3', current ? 'border-ink bg-soft' : 'border-line')}>
              <p className="font-medium">{p.name}</p>
              <p className="text-sm text-muted">${price.toLocaleString('en-US')} {period === 'annual' ? 'a year' : 'a month'}</p>
              {current ? <Badge className="mt-2" tone="accent">Current plan</Badge> : !d.can_manage ? (
                <p className="mt-2 text-xs text-muted">Ask the owner or an admin</p>
              ) : (
                <Button size="sm" className="mt-2 w-full" disabled={!ok || !!busy} loading={busy === (sub ? 'change' : 'checkout') + p.id}
                  onClick={() => (sub ? (confirm(`Switch to ${p.name} (${period})? Lemon Squeezy prorates the difference.`) && go('change', p.id)) : go('checkout', p.id))}>
                  {!ok ? 'Not available yet' : sub ? `Switch to ${p.name}` : `Upgrade to ${p.name}`}
                </Button>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-3 flex items-start gap-2 text-xs text-muted">
        <IconShield size={14} className="mt-0.5 shrink-0" />
        <span>
          Payments by Lemon Squeezy, our merchant of record, which handles sales tax and VAT. Your card never reaches Visuara.
          {d.mode === 'test' && ' Test mode: use a Lemon Squeezy test card; nothing is charged.'}
          {' '}Enterprise? <a className="underline" href={contactHref(`Visuara Enterprise for ${organization}`, '/pricing')}>Talk to us</a>.
        </span>
      </p>
    </div>
  );
}
