'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api, ApiError } from '@/lib/api';
import type { Decision, DecisionResult, DecisionVideo, Option } from '@/lib/types';
import { Alert, Badge, Button, Card, PageHeader, Progress, Skeleton, StatusBadge, cx } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconAlert, IconArrowLeft, IconArrowRight, IconCheck, IconFilm, IconInfo } from '@/components/icons';
import { COMPONENT_LABELS, categoryLabel, seconds, when } from '@/lib/format';
import { useMe, can } from '../../shell';

function ScoreRing({ score, size = 64 }: { score: number; size?: number }) {
  const r = (size - 8) / 2, c = 2 * Math.PI * r;
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="#efede8" strokeWidth="6" fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={score >= .7 ? '#15803d' : score >= .55 ? '#5b4fe9' : '#b45309'} strokeWidth="6" fill="none"
          strokeDasharray={c} strokeDashoffset={c * (1 - Math.min(1, score))} strokeLinecap="round" />
      </svg>
      <div className="absolute inset-0 grid place-items-center font-display text-xl tabular-nums">{score.toFixed(2)}</div>
    </div>
  );
}

function Bars({ components }: { components: Record<string, number> }) {
  return (
    <div className="grid grid-cols-2 gap-x-5 gap-y-2.5">
      {Object.entries(components).map(([k, v]) => (
        <div key={k}>
          <div className="flex justify-between text-xs"><span className="text-muted">{COMPONENT_LABELS[k] || k}</span><span className="tabular-nums text-ink-2">{Math.round(v * 100)}</span></div>
          <div className="mt-1 h-1.5 rounded-full bg-soft"><div className="h-full rounded-full bg-ink" style={{ width: `${Math.max(3, v * 100)}%` }} /></div>
        </div>
      ))}
    </div>
  );
}

function Matrix({ res }: { res: DecisionResult }) {
  const cell = (vid: number, pid: number) => res.matrix.find((m) => m.video_id === vid && m.product_id === pid);
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead><tr className="border-b border-line">
          <th className="px-4 py-3 text-left font-medium text-muted">Video</th>
          {res.products.map((p) => <th key={p.id} className="px-4 py-3 text-left font-medium">{p.name}<span className="block text-xs font-normal text-muted">{categoryLabel(p.category)}</span></th>)}
        </tr></thead>
        <tbody>
          {res.videos.map((v) => (
            <tr key={v.video_id} className="border-b border-line last:border-0">
              <td className="max-w-[200px] truncate px-4 py-3 text-ink-2">{v.title}</td>
              {res.products.map((p) => {
                const m = cell(v.video_id, p.id);
                const chosen = v.recommendation?.product_id === p.id;
                return (
                  <td key={p.id} className="px-4 py-3">
                    {m ? (
                      <span className={cx('inline-flex items-center gap-1.5 rounded-lg px-2 py-1 tabular-nums', chosen ? 'bg-ink text-white' : m.passes_quality ? 'bg-soft' : 'bg-warn-soft text-warn')}>
                        {chosen && <IconCheck size={13} />}{m.score.toFixed(2)}{!m.passes_quality && <IconAlert size={13} />}
                      </span>
                    ) : <span className="text-muted">—</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t border-line px-4 py-2.5 text-xs text-muted">Dark cells are the engine&apos;s choice. <IconAlert size={12} className="inline" /> marks options that would fail the quality check.</p>
    </Card>
  );
}

function OptionDetail({ o, productName }: { o: Option; productName: string }) {
  return (
    <div className="grid gap-5 md:grid-cols-[1.2fr_1fr]">
      <div className="overflow-hidden rounded-xl border border-line bg-soft">
        {o.preview_url
          // eslint-disable-next-line @next/next/no-img-element
          ? <img src={o.preview_url} alt={`${productName} placed in the scene`} className="aspect-video w-full object-cover" />
          : <div className="grid aspect-video place-items-center text-muted"><IconFilm /></div>}
        <p className="px-3 py-2 text-xs text-muted">Preview at {seconds(o.frame_second)} · placement {seconds(o.start_second)}–{seconds(o.end_second)}</p>
      </div>
      <div>
        <div className="flex items-center gap-4">
          <ScoreRing score={o.score} />
          <div>
            <p className="font-semibold">{productName}</p>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <Badge tone={o.passes_quality ? 'good' : 'warn'}>{o.passes_quality ? 'Passes quality check' : 'Would fail quality check'}</Badge>
              <Badge>{o.orientation === 'vertical' ? 'Upright surface' : o.orientation === 'horizontal' ? 'Tabletop / floor' : 'Angled surface'}</Badge>
            </div>
          </div>
        </div>
        <ul className="mt-4 space-y-1.5 text-sm text-ink-2">
          {o.reasons.map((r) => <li key={r} className="flex gap-2"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-muted" />{r}</li>)}
        </ul>
        <div className="mt-5"><Bars components={o.components} /></div>
      </div>
    </div>
  );
}

function VideoCard({ d, v, res, onApplied }: { d: Decision; v: DecisionVideo; res: DecisionResult; onApplied: () => void }) {
  const me = useMe();
  const router = useRouter();
  const toast = useToast();
  const name = (pid: number) => res.products.find((p) => p.id === pid)?.name || 'Product';
  const [selected, setSelected] = useState<number | null>(v.recommendation?.product_id ?? null);
  const [busy, setBusy] = useState<number | null>(null);
  const shown = v.options.find((o) => o.product_id === selected) || v.recommendation;

  async function apply(pid: number) {
    const existing = v.options.find((o) => o.product_id === pid)?.placement_id;
    if (existing) { router.push(`/dashboard/placements/${existing}`); return; }
    setBusy(pid);
    try {
      const r = await api<{ placement_id: number }>(`/api/decisions/${d.id}/apply`, { json: { video_id: v.video_id, product_id: pid } });
      onApplied(); router.push(`/dashboard/placements/${r.placement_id}`);
    } catch (e) { toast(e instanceof ApiError ? e.message : 'Could not create the placement', 'bad'); setBusy(null); }
  }

  return (
    <Card className="p-5 sm:p-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-muted">Video</p>
          <h3 className="text-lg font-semibold">{v.title}</h3>
        </div>
        {v.status === 'assigned' && v.recommendation && (
          <Badge tone="good"><IconCheck size={13} /> Best fit: {name(v.recommendation.product_id)}</Badge>
        )}
        {v.status === 'no_fit' && <Badge tone="warn">No confident fit</Badge>}
        {v.status === 'error' && <Badge tone="bad">Analysis failed</Badge>}
      </div>

      {v.status === 'error' && <Alert>{v.error}</Alert>}
      {v.status === 'no_fit' && <Alert tone="warn" className="mb-5">{v.explanation}</Alert>}

      {shown && <OptionDetail o={shown} productName={name(shown.product_id)} />}

      {v.options.length > 0 && (
        <div className="mt-6 border-t border-line pt-5">
          <p className="mb-3 text-sm font-medium">All products on this video</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {v.options.map((o) => (
              <div key={o.product_id} className={cx('flex items-center justify-between gap-3 rounded-xl border px-3.5 py-2.5', o.product_id === shown?.product_id ? 'border-ink' : 'border-line')}>
                <button className="min-w-0 flex-1 text-left" onClick={() => setSelected(o.product_id)}>
                  <p className="truncate text-sm font-medium">{name(o.product_id)}{o.product_id === v.recommendation?.product_id && <span className="ml-1.5 text-xs text-good">recommended</span>}</p>
                  <p className="text-xs text-muted">Score {o.score.toFixed(2)} · {o.passes_quality ? 'passes check' : 'fails check'}</p>
                </button>
                {can(me, 'editor') && o.passes_quality && (
                  <Button size="sm" variant={o.product_id === v.recommendation?.product_id ? 'primary' : 'secondary'} loading={busy === o.product_id} onClick={() => apply(o.product_id)}>
                    {o.placement_id ? 'Open placement' : 'Create placement'}
                  </Button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

export default function DecisionPage() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const { data: d, error, reload } = useApi<Decision>(`/api/decisions/${id}`, { poll: (x) => x.status === 'QUEUED' || x.status === 'RUNNING', interval: 2000 });
  const back = <Link href="/dashboard/decisions" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><IconArrowLeft size={14} /> Decisions</Link>;

  if (error) return <><PageHeader title="Decision" back={back} /><Alert>{error.message}</Alert></>;
  if (!d) return <div className="space-y-4"><Skeleton className="h-12 w-72" /><Skeleton className="h-64" /></div>;

  const running = d.status === 'QUEUED' || d.status === 'RUNNING';
  const res = d.result;
  return (
    <>
      <PageHeader title={d.name} back={back}
        description={res ? `${res.summary.assigned} of ${res.summary.videos} videos have a confident fit · ${res.summary.products_used.length} of ${res.products.length} products used · ${when(d.finished_at)}` : undefined}
        actions={running ? <Button variant="secondary" onClick={async () => { await api(`/api/decisions/${d.id}/cancel`, { method: 'POST' }).catch(() => {}); toast('Cancelling…'); reload(); }}>Cancel</Button>
          : <StatusBadge status={d.status} />} />

      {running && (
        <Card className="p-8 text-center">
          <p className="font-display text-3xl tracking-tight">{d.status === 'QUEUED' ? 'Waiting for a worker…' : 'Analysing your videos'}</p>
          <p className="mt-2 text-sm text-muted">{d.stage || 'Finding surfaces, tracking them and scoring every product.'}</p>
          <Progress value={d.progress} striped className="mx-auto mt-6 max-w-md" />
          <p className="mt-3 text-xs text-muted">You can leave this page. The result will be here when you come back.</p>
        </Card>
      )}
      {d.status === 'FAILED' && <Alert>{d.error || 'The decision failed.'}</Alert>}
      {d.status === 'CANCELLED' && <Alert tone="neutral">This decision was cancelled.</Alert>}

      {res && (
        <div className="space-y-6">
          {res.summary.unused_products.length > 0 && (
            <Alert tone="neutral" className="flex items-start gap-2">
              <IconInfo size={16} className="mt-0.5 shrink-0" />
              <span>Not placed: {res.summary.unused_products.map((p) => res.products.find((x) => x.id === p)?.name).join(', ')}. No video had a surface where they scored a confident fit ahead of the chosen products.</span>
            </Alert>
          )}
          <Matrix res={res} />
          {res.videos.map((v) => <VideoCard key={v.video_id} d={d} v={v} res={res} onApplied={reload} />)}
          <details className="rounded-2xl border border-line bg-card px-5 py-4 text-sm">
            <summary className="cursor-pointer font-medium">How these scores are calculated</summary>
            <div className="mt-3 space-y-2 text-ink-2">
              <p>Each product is scored on every usable surface: {Object.entries(res.weights).map(([k, w]) => `${COMPONENT_LABELS[k] || k} ${Math.round(w * 100)}%`).join(', ')}.
                Options that would fail the frame-by-frame quality check keep only 40% of their score and are never recommended. A video needs a score of at least {res.min_recommend_score.toFixed(2)} to get a recommendation.</p>
              <p>Products are then assigned to videos to maximise the total score{res.spread ? ', using each product at most an even share of times' : ''}.
                {res.semantic_scene_descriptions ? ' Scene descriptions from the vision model add a context signal.' : ' Scene context uses surface type only (no vision model is configured).'}</p>
              <p className="text-muted">Scores are heuristic and were developed on synthetic footage. Engine {res.engine_version}.</p>
            </div>
          </details>
          <div className="flex justify-end"><Link href="/dashboard/placements" className="inline-flex items-center gap-1.5 text-sm font-medium">Go to placements <IconArrowRight size={14} /></Link></div>
        </div>
      )}
    </>
  );
}
