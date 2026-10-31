'use client';

import { useRef, useState } from 'react';
import { api, ApiError, uploadVideo } from '@/lib/api';
import { CATEGORIES } from '@/lib/format';
import { Alert, Button, Input, Label, Progress, Select, cx } from './ui';
import { IconUpload, IconX } from './icons';
import { useToast } from './toast';

function DropZone({ accept, multiple, onFiles, children, className }: { accept: string; multiple?: boolean; onFiles: (f: File[]) => void; children: React.ReactNode; className?: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      role="button" tabIndex={0}
      onClick={() => input.current?.click()} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onFiles(Array.from(e.dataTransfer.files)); }}
      className={cx('flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-8 text-center transition',
        over ? 'border-accent bg-accent-soft' : 'border-line-2 bg-card hover:bg-soft', className)}>
      <input ref={input} type="file" hidden accept={accept} multiple={multiple} onChange={(e) => { onFiles(Array.from(e.target.files || [])); e.target.value = ''; }} />
      {children}
    </div>
  );
}

/** Add one product: image, name and category. */
export function ProductUploader({ onDone, compact }: { onDone: (id: number) => void; compact?: boolean }) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [category, setCategory] = useState('other');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function pick(files: File[]) {
    const f = files.find((x) => x.type.startsWith('image/'));
    if (!f) { setError('Choose a PNG, JPEG or WebP image.'); return; }
    setError(null); setFile(f); setPreview(URL.createObjectURL(f));
    if (!name) setName(f.name.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').slice(0, 60));
  }

  async function save() {
    if (!file) return;
    setBusy(true); setError(null);
    const form = new FormData();
    form.append('name', name.trim()); form.append('category', category); form.append('image', file);
    try {
      const r = await api<{ id: number; asset_report: { warnings: { code: string; message: string }[] } }>('/api/products', { form });
      const noAlpha = r.asset_report.warnings?.some((w) => w.code === 'NO_TRANSPARENCY');
      toast(noAlpha ? 'Product added. Tip: a cut-out PNG with a transparent background blends in better.' : 'Product added', 'good');
      setFile(null); setPreview(null); setName(''); setCategory('other');
      onDone(r.id);
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Upload failed'); }
    finally { setBusy(false); }
  }

  if (!file) {
    return (
      <div>
        <DropZone accept="image/png,image/jpeg,image/webp" onFiles={pick} className={compact ? 'py-6' : ''}>
          <IconUpload className="text-muted" />
          <p className="mt-2 text-sm font-medium">Add a product image</p>
          <p className="text-xs text-muted">PNG with transparency works best · up to 10 MB</p>
        </DropZone>
        {error && <Alert className="mt-3">{error}</Alert>}
      </div>
    );
  }
  return (
    <div className="rounded-2xl border border-line bg-card p-4">
      <div className="flex gap-4">
        <div className="grid h-24 w-24 shrink-0 place-items-center overflow-hidden rounded-xl bg-[conic-gradient(#f1f1ef_25%,#fff_0_50%,#f1f1ef_0_75%,#fff_0)] bg-[length:16px_16px]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {preview && <img src={preview} alt="" className="max-h-full max-w-full object-contain" />}
        </div>
        <div className="grid flex-1 gap-3 sm:grid-cols-2">
          <div><Label htmlFor="pname">Name</Label><Input id="pname" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></div>
          <div><Label htmlFor="pcat">Category</Label>
            <Select id="pcat" value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
            </Select>
          </div>
        </div>
      </div>
      {error && <Alert className="mt-3">{error}</Alert>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={() => { setFile(null); setPreview(null); }}>Cancel</Button>
        <Button onClick={save} loading={busy} disabled={!name.trim()}>Save product</Button>
      </div>
    </div>
  );
}

type Item = { key: string; file: File; title: string; progress: number; state: 'waiting' | 'uploading' | 'processing' | 'done' | 'error'; error?: string; ctrl?: AbortController };

/** Upload one or more videos with per-file progress. Uploads start after the rights confirmation. */
export function VideoUploader({ onUploaded }: { onUploaded: (id: number) => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const update = (key: string, patch: Partial<Item>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));

  async function run(it: Item) {
    const ctrl = new AbortController();
    update(it.key, { state: 'uploading', progress: 0, error: undefined, ctrl });
    try {
      const r = await uploadVideo(it.file, it.title, (f) => update(it.key, { progress: f, state: f >= 1 ? 'processing' : 'uploading' }), ctrl.signal);
      update(it.key, { state: 'done', progress: 1 });
      onUploaded(r.id);
    } catch (e) {
      update(it.key, { state: 'error', error: e instanceof ApiError ? e.message : 'Upload failed' });
    }
  }

  function add(files: File[]) {
    const vids = files.filter((f) => /^video\/(mp4|quicktime|webm)$/.test(f.type) || /\.(mp4|mov|webm)$/i.test(f.name));
    const next = vids.map((f) => ({ key: `${f.name}-${f.size}-${Math.random()}`, file: f, title: f.name.replace(/\.[^.]+$/, '').slice(0, 120), progress: 0, state: 'waiting' as const }));
    setItems((xs) => [...xs, ...next]);
    if (confirmed) next.forEach(run);
  }

  return (
    <div>
      <label className="mb-3 flex items-start gap-2.5 text-sm text-ink-2">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[#18181b]" checked={confirmed}
          onChange={(e) => { setConfirmed(e.target.checked); if (e.target.checked) items.filter((x) => x.state === 'waiting').forEach(run); }} />
        I own these videos or am licensed to edit them and add products to them.
      </label>
      <DropZone accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" multiple onFiles={add}>
        <IconUpload className="text-muted" />
        <p className="mt-2 text-sm font-medium">Drop videos here or click to choose</p>
        <p className="text-xs text-muted">MP4, MOV or WebM · several at once</p>
      </DropZone>
      {items.length > 0 && (
        <ul className="mt-3 space-y-2">
          {items.map((it) => (
            <li key={it.key} className="rounded-xl border border-line bg-card px-4 py-3">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="truncate font-medium">{it.title}</span>
                <span className="flex shrink-0 items-center gap-2 text-xs text-muted">
                  {it.state === 'waiting' && 'Confirm rights to start'}
                  {it.state === 'uploading' && `${Math.round(it.progress * 100)}%`}
                  {it.state === 'processing' && 'Checking video…'}
                  {it.state === 'done' && <span className="text-good">Uploaded</span>}
                  {it.state === 'error' && <button className="font-medium text-ink underline" onClick={() => run(it)}>Retry</button>}
                  {(it.state === 'uploading') && <button aria-label="Cancel upload" onClick={() => it.ctrl?.abort()} className="rounded p-0.5 hover:bg-soft"><IconX size={14} /></button>}
                </span>
              </div>
              {(it.state === 'uploading' || it.state === 'processing') && <Progress className="mt-2" value={it.progress} striped={it.state === 'processing'} />}
              {it.error && <p className="mt-1.5 text-xs text-bad">{it.error}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
