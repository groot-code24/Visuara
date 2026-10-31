'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api, ApiError, runQualityCheck } from '@/lib/api';
import type { PlacementDetail } from '@/lib/types';
import { Alert, Badge, Button, Card, Label, PageHeader, Skeleton, StatusBadge, Textarea, cx } from '@/components/ui';
import { useToast } from '@/components/toast';
import { IconArrowLeft, IconCheck, IconDownload } from '@/components/icons';
import { seconds, when } from '@/lib/format';
import { useMe, can } from '../../shell';

function Section({ n, title, state, children }: { n: number; title: string; state: 'done' | 'current' | 'todo'; children?: React.ReactNode }) {
  return (
    <div className="relative pl-11">
      <span className={cx('absolute left-0 top-0 grid h-7 w-7 place-items-center rounded-full text-xs font-semibold',
        state === 'done' ? 'bg-ink text-white' : state === 'current' ? 'bg-accent text-white' : 'bg-soft text-muted')}>
        {state === 'done' ? <IconCheck size={14} /> : n}
      </span>
      <h2 className={cx('pt-0.5 font-semibold', state === 'todo' && 'text-muted')}>{title}</h2>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}

export default function PlacementPage() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const toast = useToast();
  const { data, error, reload } = useApi<PlacementDetail>(`/api/placements/${id}`, {
    poll: (d) => d.placement.status === 'RENDERING' || d.jobs.some((j) => j.status === 'QUEUED' || j.status === 'RUNNING'), interval: 2500,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [fit, setFit] = useState(0.9);
  const [notes, setNotes] = useState('');
  const [previewT, setPreviewT] = useState<number | null>(null);

  async function act(name: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(name);
    try { await fn(); if (ok) toast(ok, 'good'); await reload(); }
    catch (e) { toast(e instanceof ApiError ? e.message : 'Something went wrong', 'bad'); await reload(); }
    finally { setBusy(null); }
  }

  const back = <Link href="/dashboard/placements" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><IconArrowLeft size={14} /> Placements</Link>;
  if (error) return <><PageHeader title="Placement" back={back} /><Alert>{error.message}</Alert></>;
  if (!data) return <div className="space-y-4"><Skeleton className="h-12 w-72" /><Skeleton className="h-96" /></div>;

  const { placement: p, quality: q, product, video } = data;
  const job = data.jobs.find((j) => j.placement_version === p.version);
  const passed = q?.status === 'PASSED';
  const approved = ['APPROVED', 'RENDERING', 'RENDERED', 'DELIVERED', 'RENDER_FAILED'].includes(p.status);
  const rendered = ['RENDERED', 'DELIVERED'].includes(p.status) && job?.status === 'SUCCEEDED';
  const terminal = ['REJECTED', 'REVOKED', 'CANCELLED', 'EXPIRED'].includes(p.status);
  const mid = q ? (p.start_second + p.end_second) / 2 : p.start_second;
  const t = previewT ?? mid;

  async function download() {
    if (!job) return;
    const r = await api<{ url: string }>(`/api/jobs/${job.id}/download-link`, { method: 'POST' });
    window.location.assign(r.url);
  }

  return (
    <>
      <PageHeader back={back} title={`${product?.name ?? 'Product'} in ${video.title}`}
        description={`Version ${p.version} · ${seconds(p.start_second)}–${seconds(p.end_second)} · created ${when(p.created_at)}`}
        actions={<StatusBadge status={p.status} />} />

      {terminal && <Alert tone={p.status === 'REJECTED' ? 'warn' : 'bad'} className="mb-6">
        {p.status === 'REJECTED' ? 'This placement was rejected.' : p.status === 'REVOKED' ? 'This placement was taken down. It can no longer be rendered or downloaded.' : `This placement is ${p.status.toLowerCase()}.`}
        {p.decision_id && <> <Link className="underline" href={`/dashboard/decisions/${p.decision_id}`}>Back to the decision</Link> to choose another option.</>}
      </Alert>}

      <div className="grid gap-6 lg:grid-cols-[1.3fr_1fr]">
        <Card className="overflow-hidden">
          {q ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img key={t} src={`/api/placements/${p.id}/preview?t=${t.toFixed(2)}`} alt="Preview frame" className="aspect-video w-full bg-soft object-contain" />
              <div className="flex items-center gap-3 border-t border-line px-4 py-3">
                <span className="text-xs text-muted">Preview</span>
                <input type="range" className="flex-1 accent-[#18181b]" min={p.start_second} max={p.end_second} step={0.1} value={t}
                  onChange={(e) => setPreviewT(Number(e.target.value))} aria-label="Preview time" />
                <span className="w-14 text-right text-xs tabular-nums text-muted">{seconds(t)}</span>
              </div>
            </>
          ) : (
            <div className="grid aspect-video place-items-center bg-soft p-8 text-center text-sm text-muted">Run the quality check to see frame previews built from the exact render plan.</div>
          )}
        </Card>

        <Card className="space-y-7 p-6">
          <Section n={1} title="Quality check" state={passed ? 'done' : 'current'}>
            {q && (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  <Badge tone={passed ? 'good' : 'bad'}>{passed ? 'Passed' : 'Failed'}</Badge>
                  <Badge>{q.frames_validated} of {q.frames_in_interval} frames validated</Badge>
                </div>
                {q.failures.map((f) => (
                  <div key={f.code} className="rounded-xl border border-bad/20 bg-bad-soft px-3.5 py-3 text-sm">
                    <p className="font-medium text-bad">{f.message}</p>
                    {f.timestamps?.length > 0 && <p className="mt-0.5 text-xs text-bad/80">At {f.timestamps.slice(0, 3).map(([a, b]) => a === b ? seconds(a) : `${seconds(a)}–${seconds(b)}`).join(', ')}</p>}
                    <p className="mt-1.5 text-ink-2">{f.recommended_action}</p>
                  </div>
                ))}
                {q.warnings?.map((w) => <p key={w.code} className="text-xs text-muted">{w.message}</p>)}
              </div>
            )}
            {p.status === 'DRAFT' && can(me, 'editor') && (
              <Button className="mt-3" variant={passed ? 'secondary' : 'primary'} loading={busy === 'qc'}
                onClick={() => act('qc', () => runQualityCheck(p.id))}>
                {q ? 'Run again' : 'Run quality check'}
              </Button>
            )}
            {busy === 'qc' && <p className="mt-2 text-xs text-muted">Measuring every frame of the placement. A long placement can take several minutes; keep this page open.</p>}
          </Section>

          <Section n={2} title="Approve" state={approved ? 'done' : passed && p.status === 'DRAFT' ? 'current' : 'todo'}>
            {p.status === 'DRAFT' && passed && (can(me, 'manager') ? (
              <div className="space-y-3">
                <div>
                  <Label hint={`${Math.round(fit * 100)}%`}>How confident are you the product fits this scene?</Label>
                  <input type="range" className="w-full accent-[#18181b]" min={0.5} max={1} step={0.05} value={fit} onChange={(e) => setFit(Number(e.target.value))} />
                  {fit < 0.75 && <p className="text-xs text-warn">Below 75% the placement cannot be approved. Reject it, or revise it from the decision.</p>}
                </div>
                <Textarea rows={2} placeholder="Notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
                <div className="flex gap-2">
                  <Button loading={busy === 'approve'} disabled={fit < 0.75}
                    onClick={() => act('approve', () => api(`/api/placements/${p.id}/approve`, { json: { fit_confidence: fit, notes } }), 'Approved for rendering')}>Approve</Button>
                  <Button variant="danger" loading={busy === 'reject'} onClick={() => {
                    const reason = prompt('Why are you rejecting this placement?');
                    if (reason && reason.trim().length >= 3) {
                      const form = new FormData(); form.append('reason', reason);
                      act('reject', () => api(`/api/placements/${p.id}/reject`, { form }), 'Rejected');
                    }
                  }}>Reject</Button>
                </div>
              </div>
            ) : <p className="text-sm text-muted">A manager, admin or owner approves placements.</p>)}
            {approved && <p className="text-sm text-muted">Approved for version {p.version}. Any edit needs a new check and approval.</p>}
          </Section>

          <Section n={3} title="Render" state={rendered ? 'done' : approved ? 'current' : 'todo'}>
            {job && (
              <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
                <StatusBadge status={job.status} />
                {job.status === 'SUCCEEDED' && <Badge tone="good">Output validated frame by frame</Badge>}
                {job.error && <span className="text-xs text-bad">{job.error.slice(0, 160)}</span>}
              </div>
            )}
            {(p.status === 'APPROVED' || p.status === 'RENDER_FAILED') && can(me, 'manager') && (
              <Button loading={busy === 'render'} onClick={() => act('render', () => api(`/api/placements/${p.id}/render`, { method: 'POST' }), 'Render started')}>
                {p.status === 'RENDER_FAILED' ? 'Retry render' : 'Start render'}
              </Button>
            )}
            {p.status === 'RENDERING' && <p className="text-sm text-muted">Rendering on a worker. This page updates automatically.</p>}
            {rendered && can(me, 'manager') && <Button onClick={() => download().catch((e) => toast(e.message, 'bad'))}><IconDownload size={16} /> Download video</Button>}
          </Section>

          {can(me, 'manager') && !terminal && p.status !== 'DRAFT' && (
            <div className="border-t border-line pt-5">
              <button className="text-sm text-bad hover:underline" onClick={() => {
                const reason = prompt('Take this placement down? Renders stop being downloadable. Reason:');
                if (reason && reason.trim().length >= 3) {
                  const form = new FormData(); form.append('reason', reason);
                  act('revoke', () => api(`/api/placements/${p.id}/revoke-rights`, { form }), 'Placement taken down');
                }
              }}>Take down this placement</button>
            </div>
          )}
        </Card>
      </div>

      {data.history.length > 0 && (
        <details className="mt-6 rounded-2xl border border-line bg-card px-5 py-4 text-sm">
          <summary className="cursor-pointer font-medium">Activity</summary>
          <ul className="mt-3 space-y-1.5 text-ink-2">
            {data.history.slice().reverse().map((h, i) => (
              <li key={i} className="flex justify-between gap-4"><span>{h.event_type.replace('placement.', '').replaceAll('_', ' ')}{h.details?.to ? ` → ${String(h.details.to).toLowerCase()}` : ''}</span><span className="text-muted">{when(h.created_at)}</span></li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
