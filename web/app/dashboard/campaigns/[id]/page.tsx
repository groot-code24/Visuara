'use client';

import Link from 'next/link';
import { use, useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import type { Product } from '@/lib/types';
import { Badge, Button, Card, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { money, pct } from '@/lib/format';
import { useMe, can } from '../../shell';

type Campaign = { id: number; name: string; status: string; version: number; budget_minor: number; currency: string; committed_minor: number; product_ids: number[];
  transitions: { from_status: string | null; to_status: string; created_at: string }[] };
type Rec = { inventory_id: number; score: number; price_minor: number; currency: string; breakdown: Record<string, unknown> };
type Reservation = { id: number; inventory_id: number; status: string; campaign_id: number | null; side: string; expires_at: number | null };
type Report = { committed_minor: number; invoiced_minor: number; paid_minor: number; requests_by_status: Record<string, number>; delivered_visible_seconds: number; events: Record<string, number>; note: string };
const NEXT: Record<string, string[]> = { DRAFT: ['SUBMITTED', 'CANCELLED'], SUBMITTED: ['ACTIVE', 'DRAFT', 'CANCELLED'], ACTIVE: ['PAUSED', 'COMPLETED', 'CANCELLED'], PAUSED: ['ACTIVE', 'CANCELLED'] };

export default function CampaignDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useMe(); const toast = useToast();
  const camp = useApi<Campaign>(`/api/campaigns/${id}`);
  const products = useApi<Product[]>('/api/products');
  const recs = useApi<{ products: { product_id: number; results: Rec[] }[] }>(camp.data && camp.data.product_ids.length ? `/api/campaigns/${id}/recommendations` : null);
  const reservations = useApi<Reservation[]>('/api/reservations');
  const report = useApi<Report>(`/api/campaigns/${id}/report`);
  const [sel, setSel] = useState<number[] | null>(null);
  async function call(path: string, json: unknown, ok: string) {
    try { await api(path, { method: path.endsWith('/products') ? 'PUT' : 'POST', json }); toast(ok, 'good'); camp.reload(); recs.reload(); reservations.reload(); report.reload(); }
    catch (e) { toast(e instanceof Error ? e.message : 'Action failed', 'bad'); }
  }
  if (!camp.data) return <Skeleton className="h-64" />;
  const c = camp.data; const chosen = sel ?? c.product_ids;
  const mine = (reservations.data || []).filter((r) => r.side === 'brand' && r.status === 'HELD' && (r.campaign_id === null || r.campaign_id === c.id));
  return (
    <>
      <PageHeader title={c.name} back={<Link href="/dashboard/campaigns" className="text-sm text-muted hover:underline">← Campaigns</Link>} actions={<StatusBadge status={c.status} />}
        description={`Committed ${money(c.committed_minor, c.currency)} of ${money(c.budget_minor, c.currency)}`} />
      {can(me, 'manager') && NEXT[c.status] && (
        <div className="mb-6 flex flex-wrap gap-2">{NEXT[c.status].map((t) => <Button key={t} size="sm" variant="secondary" onClick={() => call(`/api/campaigns/${id}/transition`, { to: t }, `Campaign ${t.toLowerCase()}`)}>{t.toLowerCase()}</Button>)}</div>
      )}
      <Card className="mb-6 p-5">
        <h2 className="mb-3 font-semibold">Products</h2>
        <div className="flex flex-wrap gap-2">
          {products.data?.map((p) => (
            <label key={p.id} className="flex items-center gap-2 rounded-xl border border-line px-3 py-1.5 text-sm">
              <input type="checkbox" checked={chosen.includes(p.id)} onChange={(e) => setSel(e.target.checked ? [...chosen, p.id] : chosen.filter((x) => x !== p.id))} />{p.name}
            </label>
          ))}
        </div>
        {can(me, 'editor') && <Button size="sm" className="mt-3" disabled={!chosen.length} onClick={() => call(`/api/campaigns/${id}/products`, { product_ids: chosen }, 'Products saved')}>Save products</Button>}
      </Card>
      <Card className="mb-6 p-5">
        <h2 className="mb-1 font-semibold">Recommended scenes</h2>
        <p className="mb-3 text-xs text-muted">Ranked by category fit × feasibility. Heuristic, not a performance prediction.</p>
        {recs.data?.products.map((p) => (
          <div key={p.product_id} className="mb-3">
            <p className="mb-1 text-sm font-medium">{products.data?.find((x) => x.id === p.product_id)?.name}</p>
            {!p.results.length ? <p className="text-sm text-muted">No listed scene fits yet.</p> : (
              <ul className="space-y-1 text-sm">{p.results.slice(0, 8).map((r) => (
                <li key={r.inventory_id} className="flex items-center justify-between gap-2">
                  <span>Item {r.inventory_id} · match {pct(r.score)}% · {money(r.price_minor, r.currency)}</span>
                  {can(me, 'manager') && <Button size="sm" variant="ghost" onClick={() => call(`/api/marketplace/inventory/${r.inventory_id}/reserve`, { campaign_id: c.id }, 'Reserved')}>Reserve</Button>}
                </li>
              ))}</ul>
            )}
          </div>
        ))}
      </Card>
      {mine.length > 0 && (
        <Card className="mb-6 p-5">
          <h2 className="mb-3 font-semibold">Your reservations</h2>
          <ul className="space-y-2 text-sm">{mine.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>Item {r.inventory_id} · held until {r.expires_at ? new Date(r.expires_at * 1000).toLocaleTimeString() : '—'}</span>
              <span className="flex gap-2">
                {can(me, 'manager') && c.product_ids.map((pid) => <Button key={pid} size="sm" onClick={() => call(`/api/campaigns/${id}/placement-requests`, { reservation_id: r.id, product_id: pid }, 'Placement requested')}>
                  Request with {products.data?.find((x) => x.id === pid)?.name || pid}</Button>)}
                {can(me, 'manager') && <Button size="sm" variant="ghost" onClick={() => call(`/api/reservations/${r.id}/release`, {}, 'Released')}>Release</Button>}
              </span>
            </li>
          ))}</ul>
        </Card>
      )}
      {report.data && (
        <Card className="p-5 text-sm">
          <h2 className="mb-3 font-semibold">Report</h2>
          <p>Committed {money(report.data.committed_minor, c.currency)} · invoiced {money(report.data.invoiced_minor, c.currency)} · paid {money(report.data.paid_minor, c.currency)}</p>
          <p className="mt-1">Delivered visible time {report.data.delivered_visible_seconds} s</p>
          <div className="mt-2 flex flex-wrap gap-2">{Object.entries(report.data.requests_by_status).map(([k, v]) => <Badge key={k}>{k.toLowerCase().replace('_', ' ')}: {v}</Badge>)}</div>
          <p className="mt-2 text-xs text-muted">{report.data.note}</p>
        </Card>
      )}
    </>
  );
}
