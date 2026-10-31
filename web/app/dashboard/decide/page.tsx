'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useApi } from '@/lib/use-api';
import { api, ApiError } from '@/lib/api';
import type { Product, Usage, Video } from '@/lib/types';
import { Alert, Button, Card, Input, Label, PageHeader, Skeleton, cx } from '@/components/ui';
import { ProductUploader, VideoUploader } from '@/components/uploaders';
import { IconArrowRight, IconCheck, IconFilm } from '@/components/icons';
import { categoryLabel, duration } from '@/lib/format';
import { useMe, can } from '../shell';

const MAX_PRODUCTS = 4;

function Step({ n, title, hint, done, children }: { n: number; title: string; hint: string; done: boolean; children: React.ReactNode }) {
  return (
    <Card className="p-5 sm:p-6">
      <div className="mb-5 flex items-start gap-3">
        <span className={cx('grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold', done ? 'bg-ink text-white' : 'bg-soft text-ink-2')}>
          {done ? <IconCheck size={14} /> : n}
        </span>
        <div><h2 className="font-semibold">{title}</h2><p className="text-sm text-muted">{hint}</p></div>
      </div>
      {children}
    </Card>
  );
}

export default function Decide() {
  const me = useMe();
  const router = useRouter();
  const products = useApi<Product[]>('/api/products');
  const videos = useApi<Video[]>('/api/videos');
  const usage = useApi<Usage>('/api/usage');
  const [pids, setPids] = useState<number[]>([]);
  const [vids, setVids] = useState<number[]>([]);
  const [name, setName] = useState('');
  const [spread, setSpread] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const maxVideos = usage.data?.plan_limits.max_videos_per_decision ?? 3;
  const editor = can(me, 'editor');

  const toggle = (list: number[], set: (x: number[]) => void, id: number, max: number) =>
    set(list.includes(id) ? list.filter((x) => x !== id) : list.length >= max ? list : [...list, id]);

  async function run() {
    setBusy(true); setError(null);
    try {
      const r = await api<{ id: number }>('/api/decisions', { json: { name, product_ids: pids, video_ids: vids, spread } });
      router.push(`/dashboard/decisions/${r.id}`);
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Could not start the decision'); setBusy(false); }
  }

  return (
    <>
      <PageHeader title="New decision" description="Choose up to four products and the videos to place them in. The engine decides which product fits where and explains why." />
      {!editor && <Alert tone="warn" className="mb-6">Your role is viewer. Ask an editor or admin to run decisions.</Alert>}
      <div className="space-y-6">
        <Step n={1} title="Products" hint={`Pick 1–${MAX_PRODUCTS}. Two to four gives the engine real choices.`} done={pids.length > 0}>
          {products.loading ? <div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><Skeleton className="h-40" /><Skeleton className="h-40" /></div> : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {products.data?.map((p) => {
                const on = pids.includes(p.id);
                const full = !on && pids.length >= MAX_PRODUCTS;
                return (
                  <button key={p.id} disabled={full} onClick={() => toggle(pids, setPids, p.id, MAX_PRODUCTS)}
                    className={cx('group relative rounded-2xl border bg-card p-3 text-left transition', on ? 'border-ink ring-2 ring-ink/10' : 'border-line hover:border-line-2', full && 'opacity-40')}>
                    <div className="grid aspect-square place-items-center overflow-hidden rounded-xl bg-[conic-gradient(#f1f1ef_25%,#fff_0_50%,#f1f1ef_0_75%,#fff_0)] bg-[length:16px_16px]">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={p.image_url} alt="" className="max-h-[85%] max-w-[85%] object-contain" />
                    </div>
                    <p className="mt-2 truncate text-sm font-medium">{p.name}</p>
                    <p className="truncate text-xs text-muted">{categoryLabel(p.category)}</p>
                    {on && <span className="absolute right-2 top-2 grid h-6 w-6 place-items-center rounded-full bg-ink text-white"><IconCheck size={14} /></span>}
                  </button>
                );
              })}
            </div>
          )}
          {editor && <div className="mt-4"><ProductUploader compact onDone={async (id) => { await products.reload(); if (pids.length < MAX_PRODUCTS) setPids((x) => [...x, id]); }} /></div>}
        </Step>

        <Step n={2} title="Videos" hint={`Pick up to ${maxVideos} on your plan. Each video gets the product that fits it best.`} done={vids.length > 0}>
          {videos.loading ? <Skeleton className="h-24" /> : videos.data?.length ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {videos.data.map((v) => {
                const on = vids.includes(v.id);
                const full = !on && vids.length >= maxVideos;
                return (
                  <button key={v.id} disabled={full} onClick={() => toggle(vids, setVids, v.id, maxVideos)}
                    className={cx('flex items-center gap-3 rounded-2xl border bg-card p-2.5 text-left transition', on ? 'border-ink ring-2 ring-ink/10' : 'border-line hover:border-line-2', full && 'opacity-40')}>
                    <div className="grid h-14 w-24 shrink-0 place-items-center overflow-hidden rounded-lg bg-soft text-muted">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" className="h-full w-full object-cover" /> : <IconFilm />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{v.title}</p>
                      <p className="text-xs text-muted">{duration(v.duration)} · {v.width}×{v.height}</p>
                    </div>
                    <span className={cx('mr-1 grid h-6 w-6 shrink-0 place-items-center rounded-full border', on ? 'border-ink bg-ink text-white' : 'border-line-2')}>{on && <IconCheck size={14} />}</span>
                  </button>
                );
              })}
            </div>
          ) : null}
          {editor && <div className="mt-4"><VideoUploader onUploaded={async (id) => { await videos.reload(); setVids((x) => (x.length < maxVideos ? [...x, id] : x)); }} /></div>}
        </Step>

        <Step n={3} title="Run" hint="Analysis runs in the background and usually takes under a minute per video." done={false}>
          <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <div><Label htmlFor="dname" hint="Optional">Name</Label><Input id="dname" value={name} maxLength={120} placeholder="e.g. Spring launch" onChange={(e) => setName(e.target.value)} /></div>
            <label className="flex h-11 items-center gap-2.5 text-sm text-ink-2">
              <input type="checkbox" className="h-4 w-4 accent-[#18181b]" checked={spread} onChange={(e) => setSpread(e.target.checked)} />
              Spread products across videos
            </label>
          </div>
          {error && <Alert className="mt-4">{error}</Alert>}
          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted">{pids.length} product{pids.length === 1 ? '' : 's'} · {vids.length} video{vids.length === 1 ? '' : 's'}
              {usage.data?.plan_limits.decisions_per_month != null && ` · ${usage.data.usage.decisions_this_month} of ${usage.data.plan_limits.decisions_per_month} decisions used this month`}</p>
            <Button size="lg" onClick={run} loading={busy} disabled={!editor || !pids.length || !vids.length}>Run decision engine <IconArrowRight size={16} /></Button>
          </div>
        </Step>
      </div>
    </>
  );
}
