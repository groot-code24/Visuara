'use client';

import { cx } from '@/components/ui';
import { ProductArt, type ProductId } from './scene';
import { useInView } from './hooks';

const LIBRARY: { file: string; tone: string; product: ProductId | null; surface?: string }[] = [
  { file: 'desk_setup.mp4', tone: 'from-[#33426a] to-[#1e2840]', product: 'aura', surface: 'screen' },
  { file: 'kitchen_tour.mp4', tone: 'from-[#c8a27c] to-[#8f7559]', product: 'oat', surface: 'tabletop' },
  { file: 'morning_routine.mp4', tone: 'from-[#ead8cf] to-[#c9a99a]', product: 'lumen', surface: 'mirror poster' },
  { file: 'studio_vlog.mp4', tone: 'from-[#d8cebf] to-[#a8927a]', product: 'north', surface: 'poster' },
  { file: 'unboxing.mp4', tone: 'from-[#9aa3b0] to-[#5f6977]', product: 'aura', surface: 'packaging face' },
  { file: 'street_walk.mp4', tone: 'from-[#6b7280] to-[#374151]', product: null },
];

/** One decision over a library: every video gets its best product, spread out, or an honest "no confident fit". */
export function LibraryMatrix() {
  const [ref, inView] = useInView<HTMLDivElement>(0.3, true);
  return (
    <div ref={ref} className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
      {LIBRARY.map((v, i) => (
        <div key={v.file} className="overflow-hidden rounded-xl border border-line bg-card">
          <div className={cx('relative aspect-video bg-gradient-to-br', v.tone)}>
            {inView && v.product && (
              <svg viewBox="0 0 80 56" className="mk-pop absolute left-1/2 top-1/2 h-1/2 -translate-x-1/2 -translate-y-1/2 drop-shadow-md" style={{ animationDelay: `${200 + i * 160}ms` }} aria-hidden>
                <ProductArt id={v.product} w={80} h={56} rx={4} />
              </svg>
            )}
            {inView && !v.product && (
              <span className="mk-fade absolute inset-x-2 top-1/2 -translate-y-1/2 rounded-md bg-black/60 px-1.5 py-1 text-center text-[10px] font-medium text-white" style={{ animationDelay: `${200 + i * 160}ms` }}>No confident fit</span>
            )}
          </div>
          <div className="px-2 py-1.5">
            <p className="truncate font-mono text-[10px] text-ink-2">{v.file}</p>
            <p className="truncate text-[10px] text-muted">{v.product ? v.surface : 'nothing forced'}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

// A deterministic per-frame tracking error trace, under the 2 px threshold.
const TRACE = Array.from({ length: 96 }, (_, i) => 0.55 + 0.28 * Math.sin(i * 0.37) + 0.18 * Math.sin(i * 1.13 + 1) + 0.1 * Math.cos(i * 2.3));

export function FrameTrace() {
  const w = 300, h = 90, max = 2.6;
  const pts = TRACE.map((v, i) => `${(i / (TRACE.length - 1)) * w},${h - (v / max) * h}`).join(' ');
  const limit = h - (2 / max) * h;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-24 w-full" aria-label="Tracking error per frame stays under the 2 pixel limit">
      <line x1="0" x2={w} y1={limit} y2={limit} stroke="#b91c1c" strokeDasharray="5 4" strokeWidth="1.2" />
      <text x={w - 2} y={limit - 4} textAnchor="end" fontSize="9" fill="#b91c1c">limit</text>
      <polyline points={`0,${h} ${pts} ${w},${h}`} fill="rgba(91,79,233,.10)" stroke="none" />
      <polyline points={pts} fill="none" stroke="#5b4fe9" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function FailureList() {
  const rows = [
    ['0:02.4', 'PARTIAL_OCCLUSION_UNSUPPORTED', 'A hand passes in front of the surface'],
    ['0:05.0', 'SCENE_CUT_IN_INTERVAL', 'Shorten the window to end before the cut'],
    ['0:07.2', 'MOTION_BLUR_UNSUPPORTED', 'Fast pan; pick a steadier stretch'],
  ];
  return (
    <ul className="space-y-2">
      {rows.map(([t, code, fix]) => (
        <li key={code} className="rounded-xl border border-line bg-canvas px-3 py-2">
          <div className="flex items-center justify-between gap-2"><span className="truncate font-mono text-[10.5px] font-semibold text-bad">{code}</span><span className="font-mono text-[10.5px] text-muted">{t}</span></div>
          <p className="mt-0.5 text-[12px] text-ink-2">{fix}</p>
        </li>
      ))}
    </ul>
  );
}

export function VersionTrail() {
  const steps = [
    { v: 'v1', label: 'Approved by Maya (manager)', tone: 'bg-good-soft text-good' },
    { v: 'v1', label: 'Rendered · delivered', tone: 'bg-good-soft text-good' },
    { v: 'v2', label: 'Window edited · approval reset', tone: 'bg-warn-soft text-warn' },
    { v: 'v2', label: 'Old render no longer downloadable', tone: 'bg-soft text-ink-2' },
  ];
  return (
    <ol className="relative space-y-2.5 border-l border-line pl-4">
      {steps.map((s, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-card bg-line-2" />
          <span className={cx('inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-medium', s.tone)}><span className="font-mono">{s.v}</span>{s.label}</span>
        </li>
      ))}
    </ol>
  );
}
