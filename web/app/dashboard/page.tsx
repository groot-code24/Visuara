'use client';

import Link from 'next/link';
import { useMe } from './shell';
import { useApi } from '@/lib/use-api';
import type { Decision, Job, Usage } from '@/lib/types';
import { ButtonLink, Card, EmptyState, Meter, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { IconArrowRight, IconBox, IconFilm, IconSpark, IconTarget } from '@/components/icons';
import { bytes, when } from '@/lib/format';

type Stats = { products: number; videos: number; decisions: number; placements: number; renders_succeeded: number; downloads: number };

export default function Overview() {
  const me = useMe();
  const stats = useApi<Stats>('/api/analytics');
  const usage = useApi<Usage>('/api/usage');
  const decisions = useApi<Decision[]>('/api/decisions');
  const jobs = useApi<Job[]>('/api/jobs');
  const first = me.name.split(' ')[0];
  const fresh = stats.data && stats.data.products === 0 && stats.data.videos === 0;

  return (
    <>
      <PageHeader title={`Hello, ${first}`} description={`${me.organization} · ${me.plan_name} plan`}
        actions={<ButtonLink href="/dashboard/decide"><IconSpark size={16} /> New decision</ButtonLink>} />

      {fresh && (
        <Card className="mb-8 overflow-hidden">
          <div className="grid gap-6 p-6 sm:p-8 md:grid-cols-[1.2fr_1fr] md:items-center">
            <div>
              <h2 className="font-display text-3xl tracking-tight">Place your first product</h2>
              <p className="mt-2 text-ink-2">Add two to four product images and a few of your videos. The decision engine will tell you which product fits where, and why.</p>
              <ButtonLink href="/dashboard/decide" className="mt-5">Start a decision <IconArrowRight size={16} /></ButtonLink>
            </div>
            <ol className="space-y-3 text-sm">
              {['Add products (image + category)', 'Upload videos you own', 'Run the decision engine', 'Approve and render'].map((s, i) => (
                <li key={s} className="flex items-center gap-3"><span className="grid h-7 w-7 place-items-center rounded-full bg-soft text-xs font-semibold">{i + 1}</span>{s}</li>
              ))}
            </ol>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          { label: 'Products', value: stats.data?.products, icon: <IconBox size={16} />, href: '/dashboard/library' },
          { label: 'Videos', value: stats.data?.videos, icon: <IconFilm size={16} />, href: '/dashboard/library?tab=videos' },
          { label: 'Decisions', value: stats.data?.decisions, icon: <IconTarget size={16} />, href: '/dashboard/decisions' },
          { label: 'Rendered videos', value: stats.data?.renders_succeeded, icon: <IconSpark size={16} />, href: '/dashboard/renders' },
        ].map((s) => (
          <Link key={s.label} href={s.href}>
            <Card className="p-5 transition hover:border-line-2">
              <div className="flex items-center gap-2 text-sm text-muted">{s.icon}{s.label}</div>
              <div className="mt-3 font-display text-4xl tabular-nums">{s.value ?? <Skeleton className="h-9 w-12" />}</div>
            </Card>
          </Link>
        ))}
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <div className="flex items-center justify-between border-b border-line px-5 py-4">
            <h2 className="font-semibold">Recent decisions</h2>
            <Link href="/dashboard/decisions" className="text-sm text-muted hover:text-ink">View all</Link>
          </div>
          {decisions.loading ? <div className="space-y-3 p-5"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
            : !decisions.data?.length ? (
              <div className="p-5"><EmptyState icon={<IconTarget />} title="No decisions yet">Run the engine on your products and videos.</EmptyState></div>
            ) : (
              <ul className="divide-y divide-line">
                {decisions.data.slice(0, 5).map((d) => (
                  <li key={d.id}>
                    <Link href={`/dashboard/decisions/${d.id}`} className="flex items-center justify-between gap-4 px-5 py-3.5 hover:bg-soft/60">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{d.name}</p>
                        <p className="text-xs text-muted">{d.product_ids.length} products · {d.video_ids.length} videos · {when(d.created_at)}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        {d.summary && <span className="text-xs text-muted">{d.summary.assigned}/{d.summary.videos} placed</span>}
                        <StatusBadge status={d.status} />
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
        </Card>

        <div className="space-y-6">
          <Card className="p-5">
            <div className="flex items-center justify-between"><h2 className="font-semibold">This month</h2>
              <Link href="/dashboard/settings#plan" className="text-sm text-muted hover:text-ink">{usage.data?.plan_limits.name} plan</Link></div>
            {usage.data ? (
              <div className="mt-4 space-y-4">
                <Meter label="Decisions" used={usage.data.usage.decisions_this_month} limit={usage.data.plan_limits.decisions_per_month} />
                <Meter label="Rendered videos" used={usage.data.usage.renders_this_month} limit={usage.data.plan_limits.renders_per_month} />
                <Meter label="Storage" used={usage.data.usage.storage_bytes} limit={usage.data.effective.storage_mb * 1048576} format={bytes} />
              </div>
            ) : <div className="mt-4 space-y-3"><Skeleton className="h-8" /><Skeleton className="h-8" /></div>}
          </Card>
          <Card>
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <h2 className="font-semibold">Recent renders</h2>
              <Link href="/dashboard/renders" className="text-sm text-muted hover:text-ink">View all</Link>
            </div>
            {!jobs.data?.length ? <p className="px-5 py-6 text-sm text-muted">Renders appear here once you approve a placement.</p> : (
              <ul className="divide-y divide-line">
                {jobs.data.slice(0, 4).map((j) => (
                  <li key={j.id} className="flex items-center justify-between gap-3 px-5 py-3">
                    <div className="min-w-0"><p className="truncate text-sm">{j.product_name} → {j.video_title}</p><p className="text-xs text-muted">{when(j.created_at)}</p></div>
                    <StatusBadge status={j.status} />
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
