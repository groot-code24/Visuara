'use client';

import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import { Alert, Badge, Button, Card, Input, PageHeader } from '@/components/ui';
import { useToast } from '@/components/toast';
import { money, newKey } from '@/lib/format';
import { useMe } from '../shell';

type Rights = { id: number; organization: string; video_id: number; holder: string; evidence_name: string; expires_on: string | null };
type Invoice = { id: number; number: string; brand: string; amount_minor: number; currency: string };
type Alloc = { id: number; number: string; publisher: string; amount_minor: number; currency: string };
type TB = { balanced: boolean; accounts: { account: string; currency: string; balance: number }[] };

export default function Admin() {
  const me = useMe(); const toast = useToast();
  const rights = useApi<Rights[]>(me.platform_admin ? '/api/admin/rights' : null);
  const invoices = useApi<Invoice[]>(me.platform_admin ? '/api/admin/invoices' : null);
  const allocs = useApi<Alloc[]>(me.platform_admin ? '/api/admin/allocations' : null);
  const tb = useApi<TB>(me.platform_admin ? '/api/admin/ledger/trial-balance' : null);
  const [ref, setRef] = useState<Record<string, string>>({});
  async function go(path: string, json: unknown, ok: string, idem = false) {
    try { await api(path, { json, headers: idem ? { 'Idempotency-Key': newKey() } : undefined }); toast(ok, 'good'); rights.reload(); invoices.reload(); allocs.reload(); tb.reload(); }
    catch (e) { toast(e instanceof Error ? e.message : 'Action failed', 'bad'); }
  }
  if (!me.platform_admin) return <Alert>Only platform administrators can open this page.</Alert>;
  return (
    <>
      <PageHeader title="Platform administration" description="Verify rights evidence, record payments received by bank transfer, and record payouts. Every action is logged." />
      <Card className="mb-6 p-5"><h2 className="mb-3 font-semibold">Rights awaiting verification</h2>
        {!rights.data?.length ? <p className="text-sm text-muted">Nothing to verify.</p> : rights.data.map((r) => (
          <div key={r.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2 text-sm last:border-0">
            <span>{r.organization} · video {r.video_id} · {r.holder}{r.expires_on && ` · until ${r.expires_on}`} · <a className="text-accent hover:underline" href={`/api/admin/rights/${r.id}/evidence`} target="_blank" rel="noreferrer">{r.evidence_name}</a></span>
            <span className="flex gap-2"><Button size="sm" onClick={() => go(`/api/admin/rights/${r.id}/decision`, { decision: 'VERIFIED' }, 'Verified')}>Verify</Button>
              <Button size="sm" variant="ghost" onClick={() => go(`/api/admin/rights/${r.id}/decision`, { decision: 'REJECTED' }, 'Rejected')}>Reject</Button></span>
          </div>
        ))}
      </Card>
      <Card className="mb-6 p-5"><h2 className="mb-3 font-semibold">Invoices awaiting payment</h2>
        {!invoices.data?.length ? <p className="text-sm text-muted">No open invoices.</p> : invoices.data.map((i) => (
          <div key={i.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
            <span>{i.number} · {i.brand} · {money(i.amount_minor, i.currency)}</span>
            <span className="flex gap-2"><Input className="w-40" placeholder="Bank reference" value={ref[`i${i.id}`] || ''} onChange={(e) => setRef({ ...ref, [`i${i.id}`]: e.target.value })} />
              <Button size="sm" disabled={(ref[`i${i.id}`] || '').length < 3} onClick={() => go(`/api/admin/invoices/${i.id}/mark-paid`, { reference: ref[`i${i.id}`] }, 'Payment recorded', true)}>Mark paid</Button></span>
          </div>
        ))}
      </Card>
      <Card className="mb-6 p-5"><h2 className="mb-3 font-semibold">Payouts due</h2>
        {!allocs.data?.length ? <p className="text-sm text-muted">Nothing payable.</p> : allocs.data.map((a) => (
          <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
            <span>{a.publisher} · {a.number} · {money(a.amount_minor, a.currency)}</span>
            <span className="flex gap-2"><Input className="w-40" placeholder="Payout reference" value={ref[`a${a.id}`] || ''} onChange={(e) => setRef({ ...ref, [`a${a.id}`]: e.target.value })} />
              <Button size="sm" disabled={(ref[`a${a.id}`] || '').length < 3} onClick={() => go(`/api/admin/allocations/${a.id}/payout`, { reference: ref[`a${a.id}`] }, 'Payout recorded', true)}>Record payout</Button></span>
          </div>
        ))}
      </Card>
      {tb.data && <Card className="p-5"><h2 className="mb-3 font-semibold">Trial balance <Badge tone={tb.data.balanced ? 'good' : 'bad'}>{tb.data.balanced ? 'balanced' : 'unbalanced'}</Badge></h2>
        <ul className="text-sm">{tb.data.accounts.map((a) => <li key={a.account + a.currency} className="flex justify-between"><span>{a.account}</span><span>{money(a.balance, a.currency)}</span></li>)}</ul></Card>}
    </>
  );
}
