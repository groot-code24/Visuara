'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import type { Video } from '@/lib/types';
import { Button, Card, EmptyState, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconSpark } from '@/components/icons';
import { duration, when } from '@/lib/format';
import { useMe, can } from '../shell';

type Analysis = { id: number; version: number; status: string; stage: string; progress: number; created_at: string; outputs: { scenes?: number; opportunities?: number } | null };

function VideoRow({ v }: { v: Video }) {
  const me = useMe(); const toast = useToast(); const [busy, setBusy] = useState(false);
  const { data, reload } = useApi<Analysis[]>(`/api/videos/${v.id}/analyses`, { poll: (d) => d.some((a) => a.status === 'QUEUED' || a.status === 'RUNNING'), interval: 2500 });
  const latest = data?.[0];
  async function start() {
    setBusy(true);
    try { await api(`/api/videos/${v.id}/analyses`, { method: 'POST' }); await reload(); toast('Analysis queued', 'good'); }
    catch (e) { toast(e instanceof Error ? e.message : 'Could not start the analysis', 'bad'); }
    finally { setBusy(false); }
  }
  return (
    <li className="grid gap-3 px-5 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0">
        <p className="truncate font-medium">{v.title}</p>
        <p className="text-sm text-muted">{duration(v.duration)} · {v.width}×{v.height}{latest && ` · analysis v${latest.version} ${when(latest.created_at)}`}
          {latest?.outputs?.scenes != null && ` · ${latest.outputs.scenes} scenes, ${latest.outputs.opportunities ?? 0} opportunities`}</p>
      </div>
      <div className="flex items-center gap-2">
        {latest && <StatusBadge status={latest.status} />}
        {latest && (latest.status === 'RUNNING' || latest.status === 'QUEUED') && <span className="text-xs text-muted">{latest.stage.toLowerCase().replace(/_/g, ' ')} {Math.round(latest.progress * 100)}%</span>}
        {latest && <Link className="text-sm text-accent hover:underline" href={`/dashboard/analyses/${latest.id}`}>Open</Link>}
        {can(me, 'editor') && <Button size="sm" variant="secondary" loading={busy} onClick={start}>{latest ? 'Analyse again' : 'Analyse'}</Button>}
      </div>
    </li>
  );
}

export default function Analyses() {
  const { data, loading } = useApi<Video[]>('/api/videos');
  return (
    <>
      <PageHeader title="Analyses" description="Each analysis runs in the background in stages and is kept as its own version. Stages without an evaluated model are reported as unavailable, never guessed." />
      {loading ? <Skeleton className="h-48" /> : !data?.length ? (
        <EmptyState icon={<IconSpark />} title="No videos yet">Upload videos in the Library, then analyse them here.</EmptyState>
      ) : <Card><ul className="divide-y divide-line">{data.map((v) => <VideoRow key={v.id} v={v} />)}</ul></Card>}
    </>
  );
}
