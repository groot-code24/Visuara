'use client';

import Link from 'next/link';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import { Button, Card, EmptyState, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconLayers } from '@/components/icons';
import { money, newKey, when } from '@/lib/format';
import { useMe, can } from '../shell';

type Req = { id: number; side: 'brand' | 'publisher'; status: string; campaign_id: number; inventory_id: number; placement_id: number | null; created_at: string;
  product: { id: number; name: string }; reservation: { price_minor: number; currency: string; status: string } | null;
  quote: { id: number; amount_minor: number; currency: string; status: string } | null; delivery: { id: number; status: string } | null; rejection_reason: string | null };

export default function Requests() {
  const me = useMe(); const toast = useToast();
  const { data, loading, reload } = useApi<Req[]>('/api/placement-requests');
  async function act(path: string, opts: { json?: unknown; headers?: Record<string, string> } = {}, ok = 'Done') {
    try { const r = await api<{ placement_id?: number }>(path, { method: 'POST', json: opts.json ?? {}, headers: opts.headers }); toast(ok, 'good'); reload(); return r; }
    catch (e) { toast(e instanceof Error ? e.message : 'Action failed', 'bad'); return null; }
  }
  async function download(r: Req) {
    const l = await api<{ url: string }>(`/api/deliveries/${r.delivery!.id}/download-link`, { method: 'POST' }).catch((e) => { toast(e.message, 'bad'); return null; });
    if (l) window.location.assign(l.url);
  }
  const manager = can(me, 'manager');
  return (
    <>
      <PageHeader title="Placement requests" description="The brand approves by requesting; the publisher approves or rejects. Approved requests are produced with the placement engine, delivered with evidence, and accepted by the brand." />
      {loading ? <Skeleton className="h-48" /> : !data?.length ? <EmptyState icon={<IconLayers />} title="No requests yet">Requests appear here for both the brand and the publisher.</EmptyState> : (
        <Card><ul className="divide-y divide-line">
          {data.map((r) => (
            <li key={r.id} className="grid gap-3 px-5 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
              <div className="min-w-0 text-sm">
                <p className="font-medium">#{r.id} · {r.product.name} · item {r.inventory_id} · {r.side === 'brand' ? 'outgoing' : 'incoming'}</p>
                <p className="text-muted">{r.reservation && money(r.reservation.price_minor, r.reservation.currency)} · {when(r.created_at)}{r.rejection_reason && ` · ${r.rejection_reason}`}
                  {r.quote && ` · quote ${r.quote.status.toLowerCase()}`}{r.delivery && ` · delivery ${r.delivery.status.toLowerCase()}`}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={r.status} />
                {manager && r.side === 'publisher' && r.status === 'REQUESTED' && <>
                  <Button size="sm" onClick={() => act(`/api/placement-requests/${r.id}/approve`, {}, 'Approved; a quote was issued')}>Approve</Button>
                  <Button size="sm" variant="ghost" onClick={() => act(`/api/placement-requests/${r.id}/reject`, { json: { reason: 'Not a fit for this content' } }, 'Rejected')}>Reject</Button></>}
                {can(me, 'editor') && r.side === 'publisher' && r.status === 'APPROVED' &&
                  <Button size="sm" onClick={async () => { const x = await act(`/api/placement-requests/${r.id}/start-production`, {}, 'Placement created'); if (x?.placement_id) window.location.assign(`/dashboard/placements/${x.placement_id}`); }}>Start production</Button>}
                {r.side === 'publisher' && r.placement_id && <Link className="text-sm text-accent hover:underline" href={`/dashboard/placements/${r.placement_id}`}>Placement</Link>}
                {manager && r.side === 'brand' && r.status === 'REQUESTED' && <Button size="sm" variant="ghost" onClick={() => act(`/api/placement-requests/${r.id}/cancel`, {}, 'Cancelled')}>Cancel</Button>}
                {manager && r.side === 'brand' && r.quote?.status === 'ISSUED' && <Button size="sm" onClick={() => act(`/api/quotes/${r.quote!.id}/accept`, { headers: { 'Idempotency-Key': newKey() } }, 'Quote accepted; invoice issued')}>Accept quote</Button>}
                {manager && r.side === 'brand' && r.delivery && <Button size="sm" variant="secondary" onClick={() => download(r)}>Download</Button>}
                {manager && r.side === 'brand' && r.status === 'DELIVERED' && <Button size="sm" onClick={() => act(`/api/placement-requests/${r.id}/accept`, {}, 'Delivery accepted')}>Accept delivery</Button>}
              </div>
            </li>
          ))}
        </ul></Card>
      )}
    </>
  );
}
