'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { cx } from '@/components/ui';
import { CLIP_SECONDS, PRODUCTS, ProductArt, SURFACES, Scene, type Overlay, type ProductId, type SurfaceId } from './scene';
import { useClock, useInView, useReducedMotion } from './hooks';

// The engine's real weights (app/decisions.py). The factor values below are example inputs for this drawing.
const WEIGHTS = { screen_time: 0.25, size: 0.2, fill: 0.15, stability: 0.15, contrast: 0.1, context: 0.15 } as const;
type Factor = keyof typeof WEIGHTS;
const LABELS: Record<Factor, string> = { screen_time: 'Screen time', size: 'On-screen size', fill: 'Shape fit', stability: 'Tracking stability', contrast: 'Contrast', context: 'Scene context' };

const SURFACE_BASE: Record<Exclude<SurfaceId, 'window'>, Pick<Record<Factor, number>, 'screen_time' | 'size' | 'stability'>> = {
  screen: { screen_time: 0.95, size: 0.74, stability: 0.96 },
  poster: { screen_time: 0.8, size: 0.66, stability: 0.93 },
  table: { screen_time: 0.7, size: 0.52, stability: 0.84 },
};
// [shape fit, contrast, scene context] for each product on each surface.
const FIT: Record<ProductId, Record<Exclude<SurfaceId, 'window'>, [number, number, number]>> = {
  aura: { screen: [0.92, 0.7, 0.95], poster: [0.62, 0.85, 0.7], table: [0.8, 0.8, 0.45] },
  north: { screen: [0.48, 0.55, 0.3], poster: [0.86, 0.9, 0.85], table: [0.4, 0.75, 0.95] },
  lumen: { screen: [0.45, 0.6, 0.35], poster: [0.88, 0.62, 0.9], table: [0.42, 0.85, 0.8] },
  oat: { screen: [0.9, 0.65, 0.3], poster: [0.6, 0.8, 0.6], table: [0.95, 0.9, 0.98] },
};
const REASON: Record<ProductId, string> = {
  aura: 'A screen is the natural home for a tech product, and this one is on camera for almost the whole clip.',
  north: 'The tall poster matches the can’s shape, and the teal stands out against the warm wall.',
  lumen: 'An upright surface at eye level suits beauty; the poster holds the bottle’s proportions.',
  oat: 'Food belongs on the table. It edges out the screen on context and shape, despite less screen time.',
};

type Scored = { surface: SurfaceId; score: number | null; factors?: Record<Factor, number> };

function scoreProduct(p: ProductId): Scored[] {
  const rows: Scored[] = (['screen', 'poster', 'table'] as const).map((s) => {
    const [fill, contrast, context] = FIT[p][s];
    const factors = { ...SURFACE_BASE[s], fill, contrast, context } as Record<Factor, number>;
    const score = (Object.keys(WEIGHTS) as Factor[]).reduce((a, k) => a + WEIGHTS[k] * factors[k], 0);
    return { surface: s, score, factors };
  });
  rows.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  return [...rows, { surface: 'window', score: null }];
}

const fmt = (s: number) => `0:${s.toFixed(1).padStart(4, '0')}`;
type Phase = 'scan' | 'score' | 'placed';

export function SceneLab() {
  const reduced = useReducedMotion();
  const [ref, inView] = useInView<HTMLDivElement>(0.25);
  const [product, setProduct] = useState<ProductId>('aura');
  const [phase, setPhase] = useState<Phase>('placed');
  const [auto, setAuto] = useState(true);
  const [run, setRun] = useState(0);
  const timers = useRef<number[]>([]);
  const t = useClock(CLIP_SECONDS, inView && !reduced);

  const scored = useMemo(() => scoreProduct(product), [product]);
  const best = scored[0];
  const bestSurface = SURFACES.find((s) => s.id === best.surface)!;

  // scan → score → place, each time a product is chosen
  useEffect(() => {
    timers.current.forEach(clearTimeout);
    if (reduced || run === 0) { setPhase('placed'); return; }
    setPhase('scan');
    timers.current = [window.setTimeout(() => setPhase('score'), 900), window.setTimeout(() => setPhase('placed'), 1900)];
    return () => timers.current.forEach(clearTimeout);
  }, [run, product, reduced]);

  // Until someone picks a product, cycle through them while on screen.
  useEffect(() => {
    if (!auto || !inView || reduced) return;
    const id = window.setInterval(() => {
      setProduct((p) => PRODUCTS[(PRODUCTS.findIndex((x) => x.id === p) + 1) % PRODUCTS.length].id);
      setRun((r) => r + 1);
    }, 6500);
    return () => clearInterval(id);
  }, [auto, inView, reduced]);

  const choose = (id: ProductId) => { setAuto(false); setProduct(id); setRun((r) => r + 1); };

  const overlays: Overlay[] = phase === 'scan' ? [] : scored.map((r) => ({
    surface: r.surface,
    tone: r.score === null ? 'refused' : r.surface === best.surface ? (phase === 'placed' ? 'winner' : 'candidate') : phase === 'placed' ? 'dim' : 'candidate',
  }));
  const status = phase === 'scan' ? 'Scanning surfaces…' : phase === 'score' ? 'Scoring 3 surfaces…' : 'Placed';
  const inWindow = t >= bestSurface.window[0] && t <= bestSurface.window[1];

  return (
    <div ref={ref} className="mk-card-light overflow-hidden rounded-[28px] border border-line bg-white text-left">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 text-[12px] text-muted sm:px-5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex gap-1.5" aria-hidden><i className="h-2.5 w-2.5 rounded-full bg-line" /><i className="h-2.5 w-2.5 rounded-full bg-line" /><i className="h-2.5 w-2.5 rounded-full bg-line" /></span>
          <span className="ml-2 truncate font-mono">decision · studio_walkthrough.mp4 · 0:08</span>
        </div>
        <span className={cx('inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 font-medium', phase === 'placed' ? 'bg-good-soft text-good' : 'bg-soft text-ink-2')}>
          <span className={cx('h-1.5 w-1.5 rounded-full', phase === 'placed' ? 'bg-good' : 'bg-ink-2')} style={phase === 'placed' ? undefined : { animation: 'mk-blink 1s infinite' }} />
          {status}
        </span>
      </div>

      <div className="grid lg:grid-cols-[1fr_320px]">
        <div className="min-w-0 p-3 sm:p-5">
          <div className="grid grid-cols-4 gap-2" role="radiogroup" aria-label="Choose a product">
            {PRODUCTS.map((p) => (
              <button key={p.id} role="radio" aria-checked={product === p.id} aria-label={p.name} onClick={() => choose(p.id)}
                className={cx('group flex min-w-0 items-center justify-center gap-2 rounded-xl border p-1.5 text-left transition sm:justify-start sm:p-2',
                  product === p.id ? 'border-accent bg-accent-soft' : 'border-line bg-white hover:border-line-2 hover:bg-soft')}>
                <svg viewBox="0 0 60 60" className="h-9 w-9 shrink-0 overflow-hidden rounded-lg sm:h-10 sm:w-10" aria-hidden><ProductArt id={p.id} w={60} h={60} rx={8} /></svg>
                <span className="hidden min-w-0 sm:block">
                  <span className="block truncate text-[13px] font-medium text-ink">{p.name}</span>
                  <span className="block truncate text-[11px] text-muted">{p.category}</span>
                </span>
              </button>
            ))}
          </div>

          <div className="relative mt-3 aspect-video overflow-hidden rounded-2xl border border-line bg-soft sm:mt-4">
            <div className="absolute inset-0" style={{ transform: `scale(1.06) translateX(${reduced ? 0 : (t / CLIP_SECONDS - 0.5) * -3}%)` }}>
              <Scene t={reduced ? undefined : t} placed={phase === 'placed' ? { product, surface: best.surface } : null} overlays={overlays} />
              {phase !== 'scan' && scored.map((r) => {
                const s = SURFACES.find((x) => x.id === r.surface)!;
                const win = r.surface === best.surface;
                return (
                  <div key={r.surface} className="mk-fade absolute -translate-y-full whitespace-nowrap" style={{ left: `${(s.anchor[0] / 640) * 100}%`, top: `${(s.anchor[1] / 360) * 100}%` }}>
                    <span className={cx('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] font-semibold sm:text-[11px]',
                      r.score === null ? 'bg-red-500/90 text-white' : win ? 'bg-accent text-white' : 'bg-black/70 text-white')}>
                      <span className="hidden sm:inline">{s.label} · {r.score === null ? 'lighting change' : s.orientation}</span>
                      <span className="sm:hidden">{s.label}</span>
                      <span className="tabular-nums opacity-90">{r.score === null ? 'refused' : r.score.toFixed(2)}</span>
                    </span>
                  </div>
                );
              })}
            </div>
            {phase === 'scan' && (
              <div className="pointer-events-none absolute inset-0">
                <div className="mk-scan absolute inset-x-0 h-1/3 bg-gradient-to-b from-transparent via-[#a99dff]/35 to-transparent" />
                <div className="absolute inset-0 bg-[linear-gradient(rgba(169,157,255,.12)_1px,transparent_1px),linear-gradient(90deg,rgba(169,157,255,.12)_1px,transparent_1px)] bg-[size:32px_32px]" />
              </div>
            )}
          </div>

          {/* timeline */}
          <div className="mt-4">
            <div className="flex justify-between font-mono text-[10px] text-muted"><span>0:00</span><span>0:04</span><span>0:08</span></div>
            <div className="relative mt-1.5 h-7 rounded-lg bg-soft">
              <div className="absolute inset-y-0 rounded-lg bg-accent/20 ring-1 ring-accent/50 transition-all duration-500"
                style={{ left: `${(bestSurface.window[0] / CLIP_SECONDS) * 100}%`, width: `${((bestSurface.window[1] - bestSurface.window[0]) / CLIP_SECONDS) * 100}%` }} />
              <div className="absolute inset-y-0 left-[38.75%] w-[2px] bg-red-500/80" title="Lighting change at 0:03.1" />
              {!reduced && <div className="absolute -inset-y-1 w-[2px] rounded bg-ink" style={{ left: `${(t / CLIP_SECONDS) * 100}%` }} />}
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
              <span>Placement window <span className="font-mono text-ink">{fmt(bestSurface.window[0])}–{fmt(bestSurface.window[1])}</span></span>
              <span className={cx('font-mono', inWindow ? 'text-accent-ink' : '')}>{reduced ? '' : `${fmt(t)} ${inWindow ? '· on screen' : ''}`}</span>
            </div>
          </div>
        </div>

        <div className="min-w-0 border-t border-line p-4 sm:p-5 lg:border-l lg:border-t-0">
          <p className="text-[11px] font-medium uppercase tracking-[.14em] text-muted">Recommendation</p>
          <div className="mt-2 flex items-end justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-[15px] font-semibold text-ink">{PRODUCTS.find((p) => p.id === product)!.name} → {bestSurface.label}</p>
              <p className="text-[12px] text-muted">{bestSurface.orientation} surface · passes the quality check</p>
            </div>
            <div className="text-right">
              <div className="font-display text-4xl leading-none text-ink tabular-nums">{phase === 'scan' ? '—' : best.score!.toFixed(2)}</div>
              <div className="text-[10px] text-muted">fit score</div>
            </div>
          </div>

          <div className="mt-4 space-y-2.5">
            {(Object.keys(WEIGHTS) as Factor[]).map((k) => {
              const v = phase === 'scan' ? 0 : best.factors![k];
              return (
                <div key={k}>
                  <div className="flex justify-between text-[11px]"><span className="text-ink-2">{LABELS[k]} <span className="text-muted">×{WEIGHTS[k]}</span></span><span className="font-mono text-muted">{phase === 'scan' ? '' : v.toFixed(2)}</span></div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-soft"><div className="h-full rounded-full bg-gradient-to-r from-accent to-trust transition-[width] duration-700 ease-out" style={{ width: `${v * 100}%` }} /></div>
                </div>
              );
            })}
          </div>

          <p className="mt-4 min-h-[54px] text-[12.5px] leading-relaxed text-ink-2">{phase === 'scan' ? 'Finding flat, trackable surfaces and following them through the clip…' : REASON[product]}</p>

          <div className="mt-3 space-y-1.5 border-t border-line pt-3 text-[12px]">
            {scored.map((r) => (
              <div key={r.surface} className="flex items-center justify-between gap-2">
                <span className={cx('truncate', r.surface === best.surface ? 'font-medium text-ink' : 'text-muted')}>{SURFACES.find((s) => s.id === r.surface)!.label}</span>
                {r.score === null
                  ? <span className="truncate font-mono text-[10.5px] text-bad">LIGHTING_CHANGE · 0:03.1</span>
                  : <span className="font-mono text-ink-2">{phase === 'scan' ? '…' : r.score.toFixed(2)}</span>}
              </div>
            ))}
          </div>
        </div>
      </div>
      <p className="border-t border-line px-4 py-2.5 text-center text-[11px] text-muted sm:px-5">
        Interactive illustration. Scores use the engine’s real weights; the scene and the factor values are examples.
      </p>
    </div>
  );
}
