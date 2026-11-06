'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';
import { api, uploadVideo } from '@/lib/api';
import { useToast } from '@/components/toast';
import { useApi } from '@/lib/use-api';
import type { Product, Video } from '@/lib/types';
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton, cx } from '@/components/ui';
import { ProductUploader, VideoUploader } from '@/components/uploaders';
import { IconBox, IconFilm } from '@/components/icons';
import { bytes, categoryLabel, duration, when } from '@/lib/format';
import { useMe, can } from '../shell';

type Version = { id: number; version: number; content_sha256: string | null; size_bytes: number; duration: number; width: number; height: number; created_at: string };

function Versions({ video, editor, onChanged }: { video: Video; editor: boolean; onChanged: () => void }) {
  const list = useApi<{ current_version: number; versions: Version[] }>(`/api/videos/${video.id}/versions`);
  const toast = useToast();
  const [progress, setProgress] = useState<number | null>(null);
  async function upload(f: File | undefined) {
    if (!f) return;
    if (!window.confirm(`Upload "${f.name}" as a new version of "${video.title}"? Earlier versions and their analyses are kept; new analyses use the new version.`)) return;
    setProgress(0);
    try { await uploadVideo(f, video.title, setProgress, undefined, video.id); toast('New version added', 'good'); list.reload(); onChanged(); }
    catch (e) { toast(e instanceof Error ? e.message : 'The new version could not be added', 'bad'); }
    finally { setProgress(null); }
  }
  return (
    <div className="mt-3 border-t border-line pt-3 text-xs">
      {!list.data ? <Skeleton className="h-10" /> : (
        <ul className="space-y-1">{list.data.versions.slice().reverse().map((v) => (
          <li key={v.id} className="flex items-center justify-between gap-2">
            <span>Version {v.version}{v.version === list.data!.current_version && <Badge tone="good" className="ml-1">current</Badge>}</span>
            <span className="text-muted">{v.width}×{v.height} · {duration(v.duration)} · {bytes(v.size_bytes || 0)} · {when(v.created_at)}{v.content_sha256 ? ` · ${v.content_sha256.slice(0, 8)}` : ''}</span>
          </li>
        ))}</ul>
      )}
      {editor && (
        <label className="mt-2 inline-flex cursor-pointer items-center gap-2 font-medium text-ink hover:underline">
          <input type="file" accept="video/mp4,video/quicktime,video/webm" className="sr-only" disabled={progress !== null} onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }} />
          {progress === null ? 'Upload a new version' : `Uploading ${Math.round(progress * 100)}%`}
        </label>
      )}
    </div>
  );
}

function Library() {
  const me = useMe();
  const tab = useSearchParams().get('tab') === 'videos' ? 'videos' : 'products';
  const router = useRouter();
  const products = useApi<Product[]>('/api/products');
  const videos = useApi<Video[]>('/api/videos');
  const editor = can(me, 'editor');
  const manager = can(me, 'manager');
  const toast = useToast();
  const [deleting, setDeleting] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  async function remove(v: Video) {
    if (!window.confirm(`Delete "${v.title}"? Its file, analyses and keyframes are removed from storage, and its unsold inventory is withdrawn. This cannot be undone.`)) return;
    setDeleting(v.id);
    try { await api(`/api/videos/${v.id}`, { method: 'DELETE' }); await videos.reload(); toast('Video deleted', 'good'); }
    catch (e) { toast(e instanceof Error ? e.message : 'Could not delete the video', 'bad'); }
    finally { setDeleting(null); }
  }

  return (
    <>
      <PageHeader title="Library" description="Your products and videos. Everything here is private to your workspace." />
      <div className="mb-6 inline-flex rounded-xl border border-line bg-card p-1 text-sm">
        {(['products', 'videos'] as const).map((t) => (
          <button key={t} onClick={() => router.replace(t === 'videos' ? '/dashboard/library?tab=videos' : '/dashboard/library')}
            className={cx('rounded-lg px-4 py-1.5 font-medium capitalize transition', tab === t ? 'bg-ink text-white' : 'text-ink-2 hover:bg-soft')}>
            {t} {t === 'products' ? products.data?.length ?? '' : videos.data?.length ?? ''}
          </button>
        ))}
      </div>

      {tab === 'products' ? (
        <div className="space-y-5">
          {editor && <ProductUploader onDone={() => products.reload()} />}
          {products.loading ? <Skeleton className="h-48" /> : !products.data?.length ? (
            <EmptyState icon={<IconBox />} title="No products yet">Add a product image above. A cut-out PNG with a transparent background blends in best.</EmptyState>
          ) : (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {products.data.map((p) => (
                <Card key={p.id} className="p-3">
                  <div className="grid aspect-square place-items-center overflow-hidden rounded-xl bg-[conic-gradient(#f1f1ef_25%,#fff_0_50%,#f1f1ef_0_75%,#fff_0)] bg-[length:16px_16px]">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={p.image_url} alt={p.name} className="max-h-[85%] max-w-[85%] object-contain" />
                  </div>
                  <p className="mt-3 truncate font-medium">{p.name}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge>{categoryLabel(p.category)}</Badge>
                    {p.asset_report?.has_transparency === false && <Badge tone="warn">No transparency</Badge>}
                  </div>
                  <p className="mt-2 text-xs text-muted">{p.asset_report?.width}×{p.asset_report?.height} · {when(p.created_at)}</p>
                </Card>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-5">
          {editor && <Card className="p-5"><VideoUploader onUploaded={() => videos.reload()} /></Card>}
          {videos.loading ? <Skeleton className="h-48" /> : !videos.data?.length ? (
            <EmptyState icon={<IconFilm />} title="No videos yet">Upload videos you own or are licensed to edit.</EmptyState>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {videos.data.map((v) => (
                <Card key={v.id} className="overflow-hidden">
                  <div className="grid aspect-video place-items-center bg-soft text-muted">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" className="h-full w-full object-cover" /> : <IconFilm size={28} />}
                  </div>
                  <div className="p-4">
                    <p className="truncate font-medium">{v.title}</p>
                    <p className="mt-1 text-xs text-muted">{duration(v.duration)} · {v.width}×{v.height} · {Math.round(v.fps)} fps · {bytes(v.size_bytes || 0)}</p>
                    <div className="mt-2 flex items-center justify-between gap-2">
                      {v.analyzed ? <Badge tone="good">Scenes analysed</Badge> : <Badge>Analysed when used in a decision</Badge>}
                      <span className="flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => setOpen(open === v.id ? null : v.id)} aria-expanded={open === v.id}>Versions</Button>
                        {manager && <Button size="sm" variant="ghost" loading={deleting === v.id} onClick={() => remove(v)} aria-label={`Delete ${v.title}`}>Delete</Button>}
                      </span>
                    </div>
                    {open === v.id && <Versions video={v} editor={editor} onChanged={() => videos.reload()} />}
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}

export default function LibraryPage() {
  return <Suspense fallback={<Skeleton className="h-64" />}><Library /></Suspense>;
}
