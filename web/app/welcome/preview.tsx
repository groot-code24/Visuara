'use client';

import { PRODUCTS, Scene, type Overlay, type ProductId, type SurfaceId } from '@/components/marketing/scene';
import { IconCheck } from '@/components/icons';
import { cx } from '@/components/ui';

export type Kind = 'direct' | 'brand' | 'publisher' | 'agency';
export type Step = 'you' | 'workspace' | 'plan';

const SURFACE: Record<Kind, SurfaceId> = { direct: 'screen', brand: 'poster', agency: 'screen', publisher: 'poster' };
const FLOW: Record<Kind, string[]> = {
  direct: ['Upload a product and a video', 'Visuara ranks every surface', 'Check, approve and render'],
  brand: ['Pick scenes that fit your product', 'The publisher approves', 'Pay, receive the finished video'],
  publisher: ['Analyse your back catalogue', 'List scenes with your prices', 'Approve requests and get paid'],
  agency: ['Switch into a client’s workspace', 'Run the placement for them', 'Client reviews, manager signs off'],
};
const HEADLINE: Record<Kind, string> = {
  direct: 'Your product, in footage you already own.',
  brand: 'Be in the scene, not the ad break.',
  publisher: 'Every scene is inventory.',
  agency: 'Every client. One place.',
};

/** The right-hand side of onboarding: a live picture of the workspace being created. Purely illustrative. */
export function Preview({ step, name, org, kind, plan, product, onProduct }: {
  step: Step; name: string; org: string; kind: Kind; plan: { name: string; price: string; renders: string };
  product: ProductId; onProduct: (p: ProductId) => void;
}) {
  const surface = SURFACE[kind];
  const scanning = step === 'you';
  const overlays: Overlay[] = scanning || kind === 'publisher'
    ? (['screen', 'poster', 'table', 'window'] as SurfaceId[]).map((s): Overlay => ({ surface: s, tone: kind === 'publisher' && !scanning && s === 'window' ? 'refused' : 'candidate' }))
    : [{ surface, tone: 'winner' }];
  const placed = scanning || kind === 'publisher' ? null : { product, surface };
  const first = name.trim().split(/\s+/)[0];
  const initials = (org.trim() || 'Your workspace').split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase()).join('');
  const done = step === 'you' ? 0 : step === 'workspace' ? 1 : 2;

  return (
    <div className="relative flex h-full flex-col overflow-hidden mk-dark px-8 py-10 xl:px-12">
      <div className="mk-glow pointer-events-none absolute inset-0 opacity-90" />
      <div className="mk-grid pointer-events-none absolute inset-0" />
      <div className="relative flex flex-1 flex-col">
        <p className="mk-eyebrow mk-eyebrow-dark">Live preview</p>
        <h2 key={`${step}-${kind}`} className="ob-step mt-4 font-display text-[34px] leading-[1.08] text-white xl:text-[40px]">
          {step === 'you' ? <>{first ? `Hi ${first}.` : 'Hi there.'}<br /><span className="mk-gradient-text">Let’s find your first placement.</span></>
            : <span className="mk-gradient-text">{HEADLINE[kind]}</span>}
        </h2>

        {/* the workspace card, updating as people type */}
        <div className="mt-7 flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[.04] p-3.5 backdrop-blur">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-[#7c6cff] to-trust text-sm font-semibold text-white">{initials || 'V'}</span>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium text-white">{org.trim() || 'Your workspace'}</p>
            <p className="truncate text-xs text-white/55">{name.trim() || 'You'} · owner</p>
          </div>
          <span className="rounded-full bg-white/10 px-2.5 py-1 text-[11px] font-medium text-white/80">{plan.name}</span>
        </div>

        {/* the scene */}
        <div className="mk-ring relative mt-5 overflow-hidden rounded-2xl bg-black">
          <div className="aspect-video"><Scene placed={placed} overlays={overlays} /></div>
          {scanning && <div className="ob-scan pointer-events-none absolute inset-x-0 top-0 h-1/3 bg-gradient-to-b from-transparent via-[#7c6cff]/25 to-transparent" />}
          <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 font-mono text-[10.5px] uppercase tracking-wider text-white/85 backdrop-blur">
            <span className={cx('h-1.5 w-1.5 rounded-full', scanning ? 'animate-pulse bg-[#a99dff]' : 'bg-trust')} />
            {scanning ? 'Scanning surfaces' : kind === 'publisher' ? '3 sellable surfaces · 1 refused' : 'Placed · every frame checked'}
          </div>
        </div>

        {/* try it: pick the product */}
        {!scanning && kind !== 'publisher' && (
          <div className="ob-step mt-4">
            <p className="text-xs text-white/50">Try it: pick a product</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {PRODUCTS.map((p) => (
                <button key={p.id} type="button" onClick={() => onProduct(p.id)} aria-pressed={product === p.id}
                  className={cx('flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition',
                    product === p.id ? 'border-white/60 bg-white/15 text-white' : 'border-white/15 text-white/65 hover:border-white/35 hover:text-white')}>
                  <span className="h-3 w-3 rounded-full" style={{ background: `linear-gradient(135deg, ${p.from}, ${p.to})` }} />{p.brand}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* what happens next */}
        <ol className="mt-auto space-y-2.5 pt-8">
          {FLOW[kind].map((t, i) => (
            <li key={`${kind}-${t}`} className="ob-step flex items-center gap-3 text-sm" style={{ animationDelay: `${i * 70}ms` }}>
              <span className={cx('grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[11px] transition-colors',
                i < done ? 'border-trust bg-trust/15 text-trust' : 'border-white/20 text-white/60')}>
                {i < done ? <IconCheck size={12} /> : i + 1}
              </span>
              <span className={i < done ? 'text-white' : 'text-white/70'}>{t}</span>
            </li>
          ))}
        </ol>
        <p className="mt-6 border-t border-white/10 pt-4 text-xs text-white/45">
          {plan.renders} · Deterministic computer vision: your product is never redrawn, and every frame is checked before anything renders.
        </p>
      </div>
    </div>
  );
}
