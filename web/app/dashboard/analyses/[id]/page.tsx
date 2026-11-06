'use client';

import Link from 'next/link';
import { use } from 'react';
import { useApi } from '@/lib/use-api';
import { api } from '@/lib/api';
import type { Opportunity } from '@/lib/types';
import { Alert, Badge, Button, Card, PageHeader, Skeleton, StatusBadge } from '@/components/ui';
import { useToast } from '@/components/toast';
import { pct, seconds } from '@/lib/format';
import { useMe, can } from '../../shell';

type Stage = { status: string; seconds?: number; detail?: Record<string, unknown> };
type Analysis = { id: number; video_id: number; version: number; status: string; stage: string; progress: number; stages: Record<string, Stage>;
  error: { stage: string; message: string } | null; outputs: { unavailable_stages?: string[] } | null; config: Record<string, unknown> | null };
type Measured = { status: string; reason?: string };
type SceneMeta = {
  camera?: Measured & { movement?: string; shake?: number };
  lighting?: Measured & { exposure?: string; colour_cast?: string; stable?: boolean };
  context?: Measured & { setting?: string; strength?: number; evidence?: Record<string, number> };
  objects?: Record<string, { tracks: number; confidence: number }>;
  safety_signals?: { object: string; confidence: number; note: string }[];
  relations?: { subject: string; relation: string; object: string }[];
  vision?: Measured & { scene_class?: string | null };
};
type Scene = { id: number; scene_index: number; start_second: number; end_second: number; boundary_source: string; description: string;
  metadata: SceneMeta; frames: { id: number; second: number; image_url: string; representative: boolean }[]; opportunities: number };
type Track = { track_id: number; label: string; start_second: number; end_second: number; confidence: number; observed_fraction: number; gaps: number[][] };
type SceneObjects = { scene_id: number; scene_index: number; tracks: Track[] };
type Factor = { value: number | null; weight: number; status: string; evidence?: string };

const words = (k: string) => k.toLowerCase().replace(/_/g, ' ');

function stageNote(k: string, s: Stage) {
  const d = s.detail || {};
  if (s.status === 'UNAVAILABLE' || s.status === 'PARTIAL') return String(d.reason || d.note || '');
  if (k === 'DETECTING_OBJECTS' && s.status === 'DONE') return `${d.model} on ${d.frames} frames, ${d.ms_per_frame} ms a frame, ${d.detections} detections`;
  if (k === 'DETECTING_PLACEMENTS' && s.status === 'DONE') {
    const r = Object.entries((d.rejected as Record<string, number>) || {});
    return `${d.candidates} surfaces followed through their scene` + (r.length ? `; rejected: ${r.map(([why, n]) => `${n} ${words(why)}`).join(', ')}` : '');
  }
  return '';
}

function SceneFacts({ m }: { m: SceneMeta }) {
  const facts: string[] = [];
  if (m.camera?.status === 'MEASURED') facts.push(`camera ${m.camera.movement}`);
  if (m.lighting?.status === 'MEASURED') facts.push(`${m.lighting.exposure} light, ${m.lighting.colour_cast}${m.lighting.stable ? '' : ', changing'}`);
  if (m.context?.status === 'MEASURED') facts.push(`setting: ${m.context.setting} (from ${Object.keys(m.context.evidence || {}).join(', ')})`);
  else if (m.context) facts.push(m.context.status === 'UNDETERMINED' ? 'setting not determined' : 'no object detection');
  const objects = Object.entries(m.objects || {});
  return (
    <div className="mt-2 space-y-1 text-xs text-muted">
      {facts.length > 0 && <p>{facts.join(' · ')}</p>}
      {objects.length > 0 && <p>Objects: {objects.map(([k, o]) => `${k}${o.tracks > 1 ? ` ×${o.tracks}` : ''}`).join(', ')}</p>}
      {m.relations && m.relations.length > 0 && <p>{m.relations.slice(0, 3).map((r) => `${r.subject} ${r.relation} ${r.object}`).join('; ')}</p>}
      {m.safety_signals && m.safety_signals.length > 0 && <p className="text-warn">For brand-safety review: {m.safety_signals.map((x) => x.note).join('; ')}</p>}
    </div>
  );
}

export default function AnalysisDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useMe(); const toast = useToast();
  const a = useApi<Analysis>(`/api/analyses/${id}`, { poll: (d) => d.status === 'QUEUED' || d.status === 'RUNNING', interval: 2000 });
  const scenes = useApi<Scene[]>(a.data?.status === 'COMPLETED' ? `/api/analyses/${id}/scenes` : null);
  const opps = useApi<(Opportunity & { score_breakdown?: Record<string, Factor> })[]>(a.data?.status === 'COMPLETED' ? `/api/opportunities?analysis_version_id=${id}` : null);
  const objects = useApi<SceneObjects[]>(a.data?.status === 'COMPLETED' ? `/api/analyses/${id}/objects` : null);

  async function review(o: Opportunity, verdict: string) {
    try { await api(`/api/opportunities/${o.id}/review`, { json: { brand_safety: verdict } }); toast(`Marked ${verdict.toLowerCase()}`, 'good'); opps.reload(); }
    catch (e) { toast(e instanceof Error ? e.message : 'Review failed', 'bad'); }
  }
  if (!a.data) return <Skeleton className="h-64" />;
  const d = a.data;
  return (
    <>
      <PageHeader title={`Analysis version ${d.version}`} back={<Link href="/dashboard/analyses" className="text-sm text-muted hover:underline">← Analyses</Link>}
        description="Scores are heuristic ranking aids, not validated predictions." actions={<StatusBadge status={d.status} />} />
      {d.error && <Alert className="mb-6">Failed at {d.error.stage.toLowerCase().replace(/_/g, ' ')}: {d.error.message}</Alert>}
      <Card className="mb-8 p-5">
        <h2 className="mb-3 font-semibold">Stages</h2>
        <ul className="grid gap-2 sm:grid-cols-2">
          {Object.entries(d.stages).map(([k, s]) => (
            <li key={k} className="flex items-center justify-between rounded-xl border border-line px-3 py-2 text-sm">
              <span>{words(k)}</span>
              <span className="flex items-center gap-2">
                {s.seconds != null && <span className="text-xs text-muted">{s.seconds.toFixed(2)} s</span>}
                <Badge tone={s.status === 'DONE' ? 'good' : s.status === 'FAILED' ? 'bad' : 'warn'}>{s.status}</Badge>
              </span>
            </li>
          ))}
        </ul>
        {Object.entries(d.stages).filter(([k, s]) => stageNote(k, s)).map(([k, s]) => (
          <p key={k} className="mt-2 text-xs text-muted">{words(k)}: {stageNote(k, s)}</p>
        ))}
      </Card>
      {scenes.data && (
        <section className="mb-8">
          <h2 className="mb-3 font-semibold">Scenes</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {scenes.data.map((s) => (
              <Card key={s.id} className="overflow-hidden">
                {s.frames.find((f) => f.representative) && <img src={s.frames.find((f) => f.representative)!.image_url} alt={`Scene ${s.scene_index + 1}`} className="aspect-video w-full object-cover" />}
                <div className="p-4 text-sm">
                  <p className="font-medium">Scene {s.scene_index + 1} · {seconds(s.start_second)}–{seconds(s.end_second)}</p>
                  <p className="text-muted">{s.description || 'No description (no vision model configured)'} · {s.opportunities} opportunities</p>
                  <SceneFacts m={s.metadata} />
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}
      {objects.data && objects.data.some((x) => x.tracks.length > 0) && (
        <section className="mb-8">
          <h2 className="mb-3 font-semibold">Objects</h2>
          <Card><ul className="divide-y divide-line text-sm">
            {objects.data.flatMap((sc) => sc.tracks.map((t) => (
              <li key={`${sc.scene_id}-${t.track_id}`} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3">
                <span><span className="font-medium">{t.label}</span> <span className="text-muted">· scene {sc.scene_index + 1}, {seconds(t.start_second)}–{seconds(t.end_second)}</span></span>
                <span className="text-xs text-muted">
                  confidence {pct(t.confidence)}% · seen in {pct(t.observed_fraction)}% of its samples
                  {t.gaps.length > 0 && ` · hidden ${t.gaps.map(([g0, g1]) => `${seconds(g0)}–${seconds(g1)}`).join(', ')}`}
                </span>
              </li>
            )))}
          </ul></Card>
          <p className="mt-2 text-xs text-muted">Tracked by detection on sampled frames. Times an object was hidden are shown as gaps, never filled in.</p>
        </section>
      )}
      {opps.data && (
        <section>
          <h2 className="mb-3 font-semibold">Placement opportunities</h2>
          {!opps.data.length ? <p className="text-sm text-muted">No placement surface was found and followed through a scene.</p> : (
            <Card><ul className="divide-y divide-line">
              {opps.data.map((o) => (
                <li key={o.id} className="grid gap-2 px-5 py-4 sm:grid-cols-[1fr_auto]">
                  <div className="min-w-0 text-sm">
                    <p className="font-medium">{o.candidate_label} · {o.placement_category.toLowerCase().replace(/_/g, ' ')} · score {pct(o.feasibility_score)}</p>
                    <p className="text-muted">{seconds(o.start_second)}–{seconds(o.end_second)}, visible {o.visible_seconds.toFixed(1)} s · {o.rendering_complexity.toLowerCase()} complexity</p>
                    <p className="mt-1 text-xs text-muted">{o.explanation}</p>
                    {o.score_breakdown && (
                      <details className="mt-2 text-xs">
                        <summary className="cursor-pointer text-muted">Score components</summary>
                        <ul className="mt-1 grid gap-1 sm:grid-cols-2">
                          {Object.entries(o.score_breakdown).map(([k, f]) => (
                            <li key={k} className="rounded-lg border border-line px-2 py-1">
                              <span className="font-medium">{words(k)}</span>{' '}
                              {f.status === 'MEASURED' && f.value != null ? pct(f.value) : <span className="text-muted">not measured</span>}
                              {f.evidence && <span className="block text-muted">{f.evidence}</span>}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={o.brand_safety === 'SAFE' ? 'good' : o.brand_safety === 'UNSAFE' ? 'bad' : 'warn'}>{o.brand_safety.replace('_', ' ')}</Badge>
                    {can(me, 'manager') && ['SAFE', 'REVIEW', 'UNSAFE'].map((v) => <Button key={v} size="sm" variant="ghost" onClick={() => review(o, v)}>{v.toLowerCase()}</Button>)}
                  </div>
                </li>
              ))}
            </ul></Card>
          )}
        </section>
      )}
    </>
  );
}
