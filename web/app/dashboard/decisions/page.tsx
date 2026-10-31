'use client';

import Link from 'next/link';
import { useApi } from '@/lib/use-api';
import type { Decision } from '@/lib/types';
import { ButtonLink, Card, EmptyState, PageHeader, Skeleton, StatusBadge, Progress } from '@/components/ui';
import { IconSpark, IconTarget } from '@/components/icons';
import { when } from '@/lib/format';

export default function Decisions() {
  const { data, loading } = useApi<Decision[]>('/api/decisions', { poll: (d) => d.some((x) => x.status === 'QUEUED' || x.status === 'RUNNING'), interval: 3000 });
  return (
    <>
      <PageHeader title="Decisions" description="Every run of the decision engine, with what it placed where."
        actions={<ButtonLink href="/dashboard/decide"><IconSpark size={16} /> New decision</ButtonLink>} />
      {loading ? <div className="space-y-3"><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
        : !data?.length ? (
          <EmptyState icon={<IconTarget />} title="No decisions yet" action={<ButtonLink href="/dashboard/decide">Start a decision</ButtonLink>}>
            Pick a few products and videos and the engine will decide which product fits where.
          </EmptyState>
        ) : (
          <Card>
            <ul className="divide-y divide-line">
              {data.map((d) => (
                <li key={d.id}>
                  <Link href={`/dashboard/decisions/${d.id}`} className="grid gap-2 px-5 py-4 hover:bg-soft/60 sm:grid-cols-[1fr_200px_110px] sm:items-center">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{d.name}</p>
                      <p className="text-sm text-muted">{d.product_ids.length} products · {d.video_ids.length} videos · {when(d.created_at)}</p>
                    </div>
                    <div className="text-sm text-ink-2">
                      {d.status === 'RUNNING' || d.status === 'QUEUED' ? <Progress value={d.progress} striped /> :
                        d.summary ? `${d.summary.assigned} of ${d.summary.videos} videos placed` : d.error ? <span className="text-bad">{d.error.slice(0, 60)}</span> : null}
                    </div>
                    <div className="sm:text-right"><StatusBadge status={d.status} /></div>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        )}
    </>
  );
}
