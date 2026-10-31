'use client';

import Link from 'next/link';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import type { Job } from '@/lib/types';
import { Button, Card, EmptyState, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconDownload } from '@/components/icons';
import { when } from '@/lib/format';
import { useMe, can } from '../shell';

export default function Renders() {
  const me = useMe();
  const toast = useToast();
  const { data, loading, reload } = useApi<Job[]>('/api/jobs', { poll: (d) => d.some((j) => j.status === 'QUEUED' || j.status === 'RUNNING'), interval: 3000 });

  async function download(j: Job) {
    try {
      const r = await api<{ url: string }>(`/api/jobs/${j.id}/download-link`, { method: 'POST' });
      window.location.assign(r.url);
    } catch (e) { toast(e instanceof Error ? e.message : 'Download unavailable', 'bad'); }
  }

  return (
    <>
      <PageHeader title="Renders" description="Rendered videos are validated frame by frame before they can be downloaded. Download links expire after five minutes." />
      {loading ? <Skeleton className="h-48" /> : !data?.length ? (
        <EmptyState icon={<IconDownload />} title="No renders yet">Approve a placement and start its render. Finished videos appear here.</EmptyState>
      ) : (
        <Card>
          <ul className="divide-y divide-line">
            {data.map((j) => (
              <li key={j.id} className="grid gap-3 px-5 py-4 sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="min-w-0">
                  <Link href={`/dashboard/placements/${j.placement_id}`} className="truncate font-medium hover:underline">{j.product_name} → {j.video_title}</Link>
                  <p className="text-sm text-muted">Version {j.placement_version} · {when(j.created_at)}{j.attempts > 1 && ` · attempt ${j.attempts}`}</p>
                  {j.error && j.status !== 'SUCCEEDED' && <p className="mt-1 text-xs text-bad">{j.error.slice(0, 200)}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={j.status} />
                  {j.status === 'SUCCEEDED' && can(me, 'manager') && <Button size="sm" variant="secondary" onClick={() => download(j)}><IconDownload size={14} /> Download</Button>}
                  {(j.status === 'QUEUED' || j.status === 'RUNNING') && can(me, 'manager') && (
                    <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/jobs/${j.id}/cancel`, { method: 'POST' }).catch(() => {}); reload(); }}>Cancel</Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
