'use client';

import Link from 'next/link';
import { useApi } from '@/lib/use-api';
import type { Placement } from '@/lib/types';
import { ButtonLink, Card, EmptyState, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { IconLayers } from '@/components/icons';
import { seconds, when } from '@/lib/format';

export default function Placements() {
  const { data, loading } = useApi<Placement[]>('/api/placements');
  return (
    <>
      <PageHeader title="Placements" description="Each placement is one product on one video. It must pass the quality check and be approved before it renders." />
      {loading ? <Skeleton className="h-48" /> : !data?.length ? (
        <EmptyState icon={<IconLayers />} title="No placements yet" action={<ButtonLink href="/dashboard/decisions">Open a decision</ButtonLink>}>
          Create placements from a decision result.
        </EmptyState>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead><tr className="border-b border-line text-left text-muted">
              <th className="px-5 py-3 font-medium">Product</th><th className="px-5 py-3 font-medium">Video</th>
              <th className="px-5 py-3 font-medium">Window</th><th className="px-5 py-3 font-medium">Quality</th>
              <th className="px-5 py-3 font-medium">Status</th><th className="px-5 py-3 font-medium">Updated</th>
            </tr></thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.id} className="border-b border-line last:border-0 hover:bg-soft/60">
                  <td className="px-5 py-3">
                    <Link href={`/dashboard/placements/${p.id}`} className="flex items-center gap-3 font-medium">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={p.image_url} alt="" className="h-9 w-9 rounded-lg bg-soft object-contain p-1" />{p.product_name}
                    </Link>
                  </td>
                  <td className="max-w-[220px] truncate px-5 py-3 text-ink-2">{p.video_title}</td>
                  <td className="px-5 py-3 tabular-nums text-ink-2">{seconds(p.start_second)}–{seconds(p.end_second)}</td>
                  <td className="px-5 py-3"><StatusBadge status={p.quality_status} /></td>
                  <td className="px-5 py-3"><StatusBadge status={p.status} />{p.version > 1 && <span className="ml-1.5 text-xs text-muted">v{p.version}</span>}</td>
                  <td className="px-5 py-3 text-muted">{when(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
