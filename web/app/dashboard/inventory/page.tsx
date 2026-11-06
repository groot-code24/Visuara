'use client';

import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import type { InventoryItem, Video } from '@/lib/types';
import { Alert, Button, Card, EmptyState, Input, Label, PageHeader, Select, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconTarget } from '@/components/icons';
import { money, pct, seconds } from '@/lib/format';
import { useMe, can } from '../shell';

function RightsForm({ videos, onDone }: { videos: Video[]; onDone: () => void }) {
  const toast = useToast(); const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true);
    const f = new FormData(e.currentTarget); const vid = f.get('video'); f.delete('video');
    if (!f.get('expires_on')) f.delete('expires_on');
    try { await api(`/api/videos/${vid}/rights`, { form: f }); toast('Rights record submitted for verification', 'good'); onDone(); (e.target as HTMLFormElement).reset(); }
    catch (err) { toast(err instanceof Error ? err.message : 'Upload failed', 'bad'); }
    finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="grid gap-3 p-5 sm:grid-cols-4 sm:items-end">
      <div><Label htmlFor="video">Video</Label><Select id="video" name="video" required>{videos.map((v) => <option key={v.id} value={v.id}>{v.title}</option>)}</Select></div>
      <div><Label htmlFor="holder">Rights holder</Label><Input id="holder" name="holder" required minLength={2} /></div>
      <div><Label htmlFor="expires_on" hint="optional">Expires on</Label><Input id="expires_on" name="expires_on" type="date" /></div>
      <div><Label htmlFor="evidence" hint="PDF, PNG or JPEG">Evidence</Label><Input id="evidence" name="evidence" type="file" accept=".pdf,.png,.jpg,.jpeg" required /></div>
      <div className="sm:col-span-4"><Button type="submit" loading={busy}>Submit rights record</Button></div>
    </form>
  );
}

function Item({ it, onChange }: { it: InventoryItem; onChange: () => void }) {
  const me = useMe(); const toast = useToast();
  const [price, setPrice] = useState(it.price_minor ? String(it.price_minor / 100) : '');
  async function act(path: string, json?: unknown) {
    try { await api(path, { json: json ?? {} }); onChange(); }
    catch (e) { toast(e instanceof Error ? e.message : 'Action failed', 'bad'); }
  }
  const o = it.opportunity;
  return (
    <li className="grid gap-3 px-5 py-4 sm:grid-cols-[96px_1fr_auto] sm:items-center">
      <img src={it.preview_url} alt="" className="hidden aspect-video w-24 rounded-lg object-cover sm:block" />
      <div className="min-w-0 text-sm">
        <p className="font-medium">{it.video_title} · {o.placement_category?.toLowerCase().replace(/_/g, ' ')} · score {pct(o.feasibility_score || 0)}</p>
        <p className="text-muted">{seconds(o.start_second || 0)}–{seconds(o.end_second || 0)} · brand safety {o.brand_safety} · {money(it.price_minor, it.currency)}</p>
        {!!it.eligibility?.length && <p className="mt-1 text-xs text-warn">Not yet listable: {it.eligibility.join('; ')}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={it.status} />
        {can(me, 'manager') && ['DRAFT', 'WITHDRAWN', 'AVAILABLE'].includes(it.status) && (
          <>
            <Input aria-label="Price in euros" className="w-24" inputMode="decimal" placeholder="EUR" value={price} onChange={(e) => setPrice(e.target.value)} />
            <Button size="sm" onClick={() => act(`/api/inventory/${it.id}/list`, { price_minor: Math.round(Number(price) * 100), currency: 'EUR' })} disabled={!(Number(price) > 0)}>List</Button>
          </>
        )}
        {can(me, 'manager') && ['DRAFT', 'AVAILABLE'].includes(it.status) && <Button size="sm" variant="ghost" onClick={() => act(`/api/inventory/${it.id}/withdraw`)}>Withdraw</Button>}
      </div>
    </li>
  );
}

export default function Inventory() {
  const me = useMe();
  const items = useApi<InventoryItem[]>('/api/inventory');
  const videos = useApi<Video[]>('/api/videos');
  return (
    <>
      <PageHeader title="Inventory" description="Each valid opportunity becomes a draft item. Review its brand safety and verify the video's rights, then list it with a price." />
      {me.org_kind !== 'publisher' && <Alert tone="warn" className="mb-6">Listing needs a publisher organization.</Alert>}
      {can(me, 'editor') && videos.data && videos.data.length > 0 && (
        <Card className="mb-8"><h2 className="px-5 pt-5 font-semibold">Rights records</h2><RightsForm videos={videos.data} onDone={items.reload} /></Card>
      )}
      {items.loading ? <Skeleton className="h-48" /> : !items.data?.length ? (
        <EmptyState icon={<IconTarget />} title="No inventory yet">Analyse a video: valid placement opportunities appear here as drafts.</EmptyState>
      ) : <Card><ul className="divide-y divide-line">{items.data.map((it) => <Item key={it.id} it={it} onChange={items.reload} />)}</ul></Card>}
    </>
  );
}
