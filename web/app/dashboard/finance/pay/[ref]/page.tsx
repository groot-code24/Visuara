'use client';

import { use, useState } from 'react';
import Link from 'next/link';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import { Alert, Button, Card, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { money } from '@/lib/format';

type Session = { provider_ref: string; invoice_id: number; number: string; amount_minor: number; currency: string; status: string; mode: string };

/** The sandbox provider's checkout: test mode only. A real provider (Stripe) shows its own page instead. */
export default function SandboxCheckout({ params }: { params: Promise<{ ref: string }> }) {
  const { ref } = use(params);
  const s = useApi<Session>(`/api/payments/sandbox/${ref}`);
  const [result, setResult] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function complete(outcome: 'succeeded' | 'failed') {
    setBusy(true);
    try { const r = await api<{ result: string }>(`/api/payments/sandbox/${ref}/complete`, { json: { outcome } }); setResult(r.result); s.reload(); }
    catch (e) { setResult(e instanceof Error ? e.message : 'The payment could not be completed'); }
    finally { setBusy(false); }
  }
  if (s.error) return <Alert>This checkout does not exist, or test-mode payments are switched off.</Alert>;
  if (!s.data) return <Skeleton className="h-48" />;
  const d = s.data;
  return (
    <>
      <PageHeader title="Test payment" back={<Link href="/dashboard/finance" className="text-sm text-muted hover:underline">← Finance</Link>}
        description="Test mode: no card is charged and no real money moves. Choose what the payment provider would report." actions={<StatusBadge status={d.status} />} />
      <Card className="max-w-lg p-6">
        <p className="text-sm text-muted">Invoice {d.number}</p>
        <p className="mb-5 text-2xl font-semibold">{money(d.amount_minor, d.currency)}</p>
        {d.status === 'CREATED' ? (
          <div className="flex flex-wrap gap-3">
            <Button disabled={busy} onClick={() => complete('succeeded')}>Pay (test)</Button>
            <Button disabled={busy} variant="secondary" onClick={() => complete('failed')}>Decline (test)</Button>
          </div>
        ) : <p className="text-sm">This checkout is {d.status.toLowerCase()}.</p>}
        {result && <p className="mt-4 text-sm text-muted">Provider result: {result.toLowerCase().replace(/_/g, ' ')}</p>}
      </Card>
    </>
  );
}
