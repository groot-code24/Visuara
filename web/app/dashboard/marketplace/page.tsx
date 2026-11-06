'use client';

import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import type { InventoryItem } from '@/lib/types';
import { Button, Card, EmptyState, Input, Label, PageHeader, Select, Skeleton } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconTarget } from '@/components/icons';
import { CATEGORIES, money, pct, seconds } from '@/lib/format';
import { useMe, can } from '../shell';

type Campaign = { id: number; name: string; status: string };

export default function Marketplace() {
  const me = useMe(); const toast = useToast();
  const [cat, setCat] = useState(''); const [minScore, setMinScore] = useState('0'); const [maxPrice, setMaxPrice] = useState('');
  const q = new URLSearchParams({ min_score: String(Number(minScore) / 100 || 0), ...(cat ? { product_category: cat } : {}), ...(Number(maxPrice) > 0 ? { max_price_minor: String(Math.round(Number(maxPrice) * 100)) } : {}) });
  const items = useApi<InventoryItem[]>(`/api/marketplace/inventory?${q}`);
  const campaigns = useApi<Campaign[]>('/api/campaigns');
  const [campaign, setCampaign] = useState('');
  async function reserve(it: InventoryItem) {
    try {
      const r = await api<{ expires_at: number }>(`/api/marketplace/inventory/${it.id}/reserve`, { json: { campaign_id: campaign ? Number(campaign) : null } });
      toast(`Reserved until ${new Date(r.expires_at * 1000).toLocaleTimeString()}. Request the placement from your campaign.`, 'good'); items.reload();
    } catch (e) { toast(e instanceof Error ? e.message : 'Reservation failed', 'bad'); items.reload(); }
  }
  return (
    <>
      <PageHeader title="Marketplace" description="Scenes publishers have listed, with verified rights and reviewed brand safety. A reservation holds an item for you while you request the placement." />
      <Card className="mb-6 grid gap-3 p-5 sm:grid-cols-4">
        <div><Label htmlFor="cat">Your product category</Label><Select id="cat" value={cat} onChange={(e) => setCat(e.target.value)}><option value="">Any</option>{CATEGORIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</Select></div>
        <div><Label htmlFor="score">Minimum score (%)</Label><Input id="score" type="number" min={0} max={100} value={minScore} onChange={(e) => setMinScore(e.target.value)} /></div>
        <div><Label htmlFor="price">Maximum price (EUR)</Label><Input id="price" type="number" min={0} value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} /></div>
        <div><Label htmlFor="camp">Reserve for campaign</Label><Select id="camp" value={campaign} onChange={(e) => setCampaign(e.target.value)}><option value="">None yet</option>
          {campaigns.data?.filter((c) => ['DRAFT', 'SUBMITTED', 'ACTIVE'].includes(c.status)).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></div>
      </Card>
      {items.loading ? <Skeleton className="h-48" /> : !items.data?.length ? (
        <EmptyState icon={<IconTarget />} title="Nothing matches">No listed inventory matches these filters right now.</EmptyState>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.data.map((it) => (
            <Card key={it.id} className="overflow-hidden">
              <img src={it.preview_url} alt="" className="aspect-video w-full object-cover" />
              <div className="space-y-1 p-4 text-sm">
                <p className="font-medium">{it.publisher} · {money(it.price_minor, it.currency)}</p>
                <p className="text-muted">{it.opportunity.placement_category?.toLowerCase().replace(/_/g, ' ')} · {it.opportunity.region?.toLowerCase().replace('_', ' ')} · score {pct(it.opportunity.feasibility_score || 0)}</p>
                <p className="text-muted">{seconds(it.opportunity.start_second || 0)}–{seconds(it.opportunity.end_second || 0)}, visible {(it.opportunity.visible_seconds || 0).toFixed(1)} s</p>
                {cat && <p className="text-muted">Fit for {cat}: {pct(it.opportunity.product_compatibility?.[cat] || 0)}%</p>}
                {can(me, 'manager') && <Button size="sm" className="mt-2" onClick={() => reserve(it)}>Reserve</Button>}
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
