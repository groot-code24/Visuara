'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import { Button, Card, EmptyState, Input, Label, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconLayers } from '@/components/icons';
import { money } from '@/lib/format';
import { useMe, can } from '../shell';

type Campaign = { id: number; name: string; status: string; budget_minor: number; currency: string; committed_minor: number; product_ids: number[] };

export default function Campaigns() {
  const me = useMe(); const toast = useToast();
  const { data, loading, reload } = useApi<Campaign[]>('/api/campaigns');
  const [name, setName] = useState(''); const [budget, setBudget] = useState('');
  async function create(e: React.FormEvent) {
    e.preventDefault();
    try { await api('/api/campaigns', { json: { name, budget_minor: Math.round(Number(budget || 0) * 100), currency: 'EUR' } }); setName(''); setBudget(''); reload(); }
    catch (err) { toast(err instanceof Error ? err.message : 'Could not create the campaign', 'bad'); }
  }
  return (
    <>
      <PageHeader title="Campaigns" description="A campaign groups your products, targeting and budget. Its placement requests go to publishers for approval." />
      {can(me, 'editor') && (
        <Card className="mb-6 p-5">
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-[1fr_200px_auto] sm:items-end">
            <div><Label htmlFor="n">Name</Label><Input id="n" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /></div>
            <div><Label htmlFor="b">Budget (EUR)</Label><Input id="b" type="number" min={0} value={budget} onChange={(e) => setBudget(e.target.value)} /></div>
            <Button type="submit">Create campaign</Button>
          </form>
        </Card>
      )}
      {loading ? <Skeleton className="h-40" /> : !data?.length ? <EmptyState icon={<IconLayers />} title="No campaigns yet">Create one to start finding scenes for your products.</EmptyState> : (
        <Card><ul className="divide-y divide-line">
          {data.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-3 px-5 py-4">
              <div><Link href={`/dashboard/campaigns/${c.id}`} className="font-medium hover:underline">{c.name}</Link>
                <p className="text-sm text-muted">{c.product_ids.length} products · committed {money(c.committed_minor, c.currency)} of {money(c.budget_minor, c.currency)}</p></div>
              <StatusBadge status={c.status} />
            </li>
          ))}
        </ul></Card>
      )}
    </>
  );
}
