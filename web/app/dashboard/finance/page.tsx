'use client';

import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import { Button, Card, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { money, newKey, when } from '@/lib/format';
import { useMe, can } from '../shell';

type Invoice = { id: number; number: string; amount_minor: number; currency: string; status: string; due_on: string; issued_at: string; brand_org_id: number };
type PayConfig = { provider: string | null; mode: 'test' | 'live' | null; ready: boolean };
type Earnings = { allocations: { id: number; number: string; amount_minor: number; currency: string; status: string }[]; totals: Record<string, Record<string, number>> };
type Line = { id: number; account: string; direction: string; amount_minor: number; currency: string; memo: string; created_at: string };

export default function Finance() {
  const invoices = useApi<Invoice[]>('/api/invoices');
  const earnings = useApi<Earnings>('/api/earnings');
  const ledger = useApi<Line[]>('/api/ledger');
  const pay = useApi<PayConfig>('/api/payments/config');
  const me = useMe(); const toast = useToast();
  async function checkout(i: Invoice) {
    try {
      const r = await api<{ checkout_url: string }>(`/api/invoices/${i.id}/checkout`, { method: 'POST', headers: { 'Idempotency-Key': newKey() } });
      window.location.assign(r.checkout_url);
    } catch (e) { toast(e instanceof Error ? e.message : 'Payment could not start', 'bad'); }
  }
  const canPay = pay.data?.ready && can(me, 'manager');
  return (
    <>
      <PageHeader title="Finance" description={pay.data?.ready
        ? `Invoices are paid through ${pay.data.provider}${pay.data.mode === 'test' ? ' in test mode: no real money moves' : ''}, or by bank transfer. Every amount is recorded in an append-only, double-entry ledger.`
        : 'Invoices are paid by bank transfer and recorded by Visuara. Every amount is recorded in an append-only, double-entry ledger.'} />
      <section className="mb-8">
        <h2 className="mb-3 font-semibold">Invoices</h2>
        {!invoices.data ? <Skeleton className="h-24" /> : !invoices.data.length ? <p className="text-sm text-muted">No invoices yet.</p> : (
          <Card><ul className="divide-y divide-line">{invoices.data.map((i) => (
            <li key={i.id} className="flex items-center justify-between gap-3 px-5 py-3 text-sm"><span>{i.number} · {money(i.amount_minor, i.currency)} · due {i.due_on}</span>
              <span className="flex items-center gap-2">{canPay && i.status === 'ISSUED' && i.brand_org_id === me.org_id && <Button size="sm" onClick={() => checkout(i)}>Pay{pay.data?.mode === 'test' ? ' (test)' : ''}</Button>}<StatusBadge status={i.status} /></span></li>
          ))}</ul></Card>
        )}
      </section>
      {earnings.data && earnings.data.allocations.length > 0 && (
        <section className="mb-8">
          <h2 className="mb-3 font-semibold">Earnings</h2>
          <Card className="p-5 text-sm">{Object.entries(earnings.data.totals).map(([cur, t]) => (
            <p key={cur}>{Object.entries(t).map(([s, v]) => `${s.toLowerCase()} ${money(v, cur)}`).join(' · ')}</p>
          ))}</Card>
        </section>
      )}
      <section>
        <h2 className="mb-3 font-semibold">Ledger</h2>
        {!ledger.data ? <Skeleton className="h-24" /> : !ledger.data.length ? <p className="text-sm text-muted">No ledger entries for this organization.</p> : (
          <Card><ul className="divide-y divide-line">{ledger.data.map((l) => (
            <li key={l.id} className="grid grid-cols-[1fr_auto] gap-2 px-5 py-2 text-sm"><span>{l.memo} <span className="text-muted">· {l.account} · {when(l.created_at)}</span></span>
              <span className={l.direction === 'DEBIT' ? '' : 'text-good'}>{l.direction === 'DEBIT' ? '' : '−'}{money(l.amount_minor, l.currency)}</span></li>
          ))}</ul></Card>
        )}
      </section>
    </>
  );
}
