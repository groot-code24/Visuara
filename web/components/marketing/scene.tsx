'use client';

import { useId, type ReactNode } from 'react';

// An illustrated 8-second studio shot (640×360 units) with the kinds of surfaces the decision engine looks for.
// Everything here is a drawing for the marketing site, not output of the engine.

export type ProductId = 'aura' | 'north' | 'lumen' | 'oat';
export type SurfaceId = 'screen' | 'poster' | 'table' | 'window';

export const PRODUCTS: { id: ProductId; name: string; brand: string; category: string; from: string; to: string; ink: string }[] = [
  { id: 'aura', name: 'Headphones', brand: 'AURA', category: 'Tech', from: '#5b4fe9', to: '#9a8ffb', ink: '#ffffff' },
  { id: 'north', name: 'Cold brew', brand: 'NORTH', category: 'Beverage', from: '#0f766e', to: '#2dd4bf', ink: '#ffffff' },
  { id: 'lumen', name: 'Face serum', brand: 'LUMEN', category: 'Beauty', from: '#f6d5c8', to: '#e79d8b', ink: '#4a2a22' },
  { id: 'oat', name: 'Granola', brand: 'OAT & EMBER', category: 'Food', from: '#f59e0b', to: '#ea580c', ink: '#ffffff' },
];

type Box = { x: number; y: number; w: number; h: number };
export type Surface = {
  id: SurfaceId; label: string; orientation: string;
  outline: [number, number][];            // polygon in scene units
  box?: Box;                              // axis-aligned placement area
  matrix?: [number, number, number, number, number, number]; // maps a 200×140 product card onto an angled surface
  window: [number, number];               // seconds the surface is usable in the 8 s clip
  anchor: [number, number];               // where its label sits
};

export const SURFACES: Surface[] = [
  { id: 'screen', label: 'Screen', orientation: 'horizontal', outline: [[86, 92], [282, 92], [282, 210], [86, 210]], box: { x: 86, y: 92, w: 196, h: 118 }, window: [0.4, 7.6], anchor: [86, 84] },
  { id: 'poster', label: 'Poster', orientation: 'vertical', outline: [[442, 48], [556, 48], [556, 196], [442, 196]], box: { x: 442, y: 48, w: 114, h: 148 }, window: [1.2, 6.4], anchor: [442, 40] },
  { id: 'table', label: 'Tabletop', orientation: 'angled', outline: [[318, 234], [432, 234], [454, 258], [340, 258]], matrix: [0.57, 0, 22 / 140, 24 / 140, 318, 234], window: [1.4, 6.6], anchor: [318, 278] },
  { id: 'window', label: 'Window', orientation: 'vertical', outline: [[318, 40], [410, 40], [410, 152], [318, 152]], window: [0, 3.1], anchor: [318, 174] },
];

export const CLIP_SECONDS = 8;

function ProductIcon({ id, color, accent }: { id: ProductId; color: string; accent: string }) {
  switch (id) {
    case 'aura':
      return (<g fill={color}><path d="M24 60V50a26 26 0 0 1 52 0v10" fill="none" stroke={color} strokeWidth="7" strokeLinecap="round" /><rect x="14" y="54" width="18" height="28" rx="7" /><rect x="68" y="54" width="18" height="28" rx="7" /></g>);
    case 'north':
      return (<g><rect x="33" y="12" width="34" height="76" rx="8" fill={color} /><rect x="33" y="38" width="34" height="24" fill={accent} /><rect x="38" y="16" width="24" height="4" rx="2" fill={accent} opacity=".5" /></g>);
    case 'lumen':
      return (<g><rect x="42" y="8" width="16" height="18" rx="8" fill={color} /><rect x="45" y="24" width="10" height="10" fill={color} opacity=".8" /><rect x="30" y="34" width="40" height="56" rx="12" fill={color} opacity=".92" /><rect x="38" y="52" width="24" height="16" rx="3" fill={accent} opacity=".55" /></g>);
    case 'oat':
      return (<g><path d="M14 50h72a36 32 0 0 1-72 0Z" fill={color} />{[[30, 44], [42, 40], [55, 43], [68, 41], [36, 36], [50, 33], [62, 35]].map(([cx, cy]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="5" fill={accent} opacity=".85" />)}</g>);
  }
}

/** A product card drawn to fill a w×h area: gradient background, icon, brand name. */
export function ProductArt({ id, w, h, rx = 3 }: { id: ProductId; w: number; h: number; rx?: number }) {
  const p = PRODUCTS.find((x) => x.id === id)!;
  const gid = useId().replace(/:/g, '');
  const s = Math.min(w, h) * 0.5;
  const fs = Math.max(6, Math.min(w, h) * 0.12);
  const cy = h * 0.44;
  return (
    <g>
      <defs><linearGradient id={`g${gid}`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor={p.from} /><stop offset="1" stopColor={p.to} /></linearGradient></defs>
      <rect width={w} height={h} rx={rx} fill={`url(#g${gid})`} />
      <rect width={w} height={h} rx={rx} fill="none" stroke="rgba(0,0,0,.18)" />
      <g transform={`translate(${w / 2 - s / 2} ${cy - s / 2}) scale(${s / 100})`}><ProductIcon id={id} color={p.ink} accent={p.from} /></g>
      <text x={w / 2} y={cy + s / 2 + fs * 1.25} textAnchor="middle" fontSize={fs} fontWeight="700" letterSpacing={fs * 0.08} fill={p.ink} fontFamily="Inter, system-ui, sans-serif">{p.brand}</text>
    </g>
  );
}

function Placement({ surface, product }: { surface: Surface; product: ProductId }) {
  if (surface.box) {
    const b = surface.box;
    return <g transform={`translate(${b.x} ${b.y})`}><ProductArt id={product} w={b.w} h={b.h} /></g>;
  }
  if (surface.matrix) return <g transform={`matrix(${surface.matrix.join(' ')})`}><ProductArt id={product} w={200} h={140} rx={6} /></g>;
  return null;
}

export type Overlay = { surface: SurfaceId; tone: 'candidate' | 'winner' | 'refused' | 'dim' };

/** The scene. `placed` puts a product on a surface (shown only inside the surface's time window when `t` is given). */
export function Scene({ placed, overlays = [], t, children }: {
  placed?: { product: ProductId; surface: SurfaceId } | null; overlays?: Overlay[]; t?: number; children?: ReactNode;
}) {
  const surf = placed ? SURFACES.find((s) => s.id === placed.surface)! : null;
  const visible = !!surf && (t === undefined || (t >= surf.window[0] && t <= surf.window[1]));
  const u = useId().replace(/:/g, '');
  const lightDip = t !== undefined && t > 3.1 && t < 4.4;    // the lighting change that makes the window unusable
  return (
    <svg viewBox="0 0 640 360" className="block h-full w-full" role="img" aria-label="Illustrated studio scene with a monitor, a poster, a window and a desk">
      <defs>
        <linearGradient id={`${u}wall`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ece5d9" /><stop offset="1" stopColor="#d8cebf" /></linearGradient>
        <linearGradient id={`${u}floor`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#b59c7f" /><stop offset="1" stopColor="#8f7559" /></linearGradient>
        <linearGradient id={`${u}sky`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#bcd7f0" /><stop offset="1" stopColor="#f3e7d2" /></linearGradient>
        <linearGradient id={`${u}idle`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#1e2840" /><stop offset="1" stopColor="#33426a" /></linearGradient>
        <radialGradient id={`${u}spill`} cx=".55" cy=".2" r=".6"><stop offset="0" stopColor="#fff6e4" stopOpacity=".75" /><stop offset="1" stopColor="#fff6e4" stopOpacity="0" /></radialGradient>
        <linearGradient id={`${u}art`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#d9cbb5" /><stop offset="1" stopColor="#bfae95" /></linearGradient>
      </defs>
      <rect width="640" height="252" fill={`url(#${u}wall)`} />
      <rect width="640" height="252" fill={`url(#${u}spill)`} />
      <rect y="250" width="640" height="110" fill={`url(#${u}floor)`} />
      <rect y="248" width="640" height="4" fill="#c9bba6" />

      {/* window */}
      <rect x="312" y="34" width="104" height="124" rx="4" fill="#cfc3b1" />
      <rect x="318" y="40" width="92" height="112" fill={`url(#${u}sky)`} />
      <rect x="318" y="40" width="92" height="112" fill="#000" opacity={lightDip ? 0.32 : 0} style={{ transition: 'opacity .35s' }} />
      <path d="M364 40v112M318 96h92" stroke="#cfc3b1" strokeWidth="4" />

      {/* poster frame + default art */}
      <rect x="436" y="42" width="126" height="160" rx="3" fill="#2a2522" />
      <rect x="442" y="48" width="114" height="148" fill={`url(#${u}art)`} />
      <circle cx="486" cy="100" r="26" fill="#a8927a" /><rect x="470" y="140" width="64" height="8" rx="4" fill="#a8927a" opacity=".7" />

      {/* plant */}
      <path d="M598 250c-4-40 6-70 20-96M600 250c-20-30-40-44-60-50M602 250c12-26 30-40 34-44" stroke="#3f6b4a" strokeWidth="5" fill="none" strokeLinecap="round" />
      <ellipse cx="618" cy="152" rx="14" ry="7" fill="#4f8a5d" transform="rotate(-50 618 152)" /><ellipse cx="542" cy="198" rx="16" ry="7" fill="#4f8a5d" transform="rotate(20 542 198)" /><ellipse cx="634" cy="204" rx="12" ry="6" fill="#4f8a5d" transform="rotate(-30 634 204)" />
      <path d="M582 250h40l-6 40h-28Z" fill="#7a5c48" />

      {/* desk */}
      <path d="M40 226h430l30 36H10Z" fill="#7b5a3f" />
      <path d="M10 262h490v30H10Z" fill="#5e4430" />
      <rect x="24" y="292" width="10" height="56" fill="#4a3526" /><rect x="476" y="292" width="10" height="56" fill="#4a3526" />

      {/* monitor */}
      <rect x="78" y="84" width="212" height="134" rx="8" fill="#17171c" />
      <rect x="86" y="92" width="196" height="118" fill={`url(#${u}idle)`} />
      <path d="M100 110h70M100 122h110M100 134h90" stroke="#5c6c9a" strokeWidth="5" strokeLinecap="round" opacity=".6" />
      <rect x="176" y="218" width="16" height="10" fill="#2a2a31" /><ellipse cx="184" cy="230" rx="36" ry="5" fill="#2a2a31" />

      {/* mug on desk */}
      <rect x="226" y="208" width="22" height="24" rx="4" fill="#efe9df" /><path d="M248 214a7 7 0 0 1 0 14" stroke="#efe9df" strokeWidth="4" fill="none" />

      {visible && surf && placed && <g className="mk-pop" style={{ transformOrigin: `${surf.anchor[0]}px ${surf.anchor[1]}px` }}><Placement surface={surf} product={placed.product} /></g>}

      {overlays.map((o) => {
        const s = SURFACES.find((x) => x.id === o.surface)!;
        const pts = s.outline.map((p) => p.join(',')).join(' ');
        const stroke = o.tone === 'winner' ? '#a99dff' : o.tone === 'refused' ? '#f87171' : o.tone === 'dim' ? 'rgba(255,255,255,.35)' : '#ffffff';
        return (
          <polygon key={o.surface} points={pts} fill={o.tone === 'winner' ? 'rgba(124,108,255,.10)' : o.tone === 'refused' ? 'rgba(248,113,113,.10)' : 'rgba(255,255,255,.06)'}
            stroke={stroke} strokeWidth={o.tone === 'winner' ? 3 : 2} strokeDasharray={o.tone === 'winner' ? undefined : '7 5'} className={o.tone === 'winner' ? undefined : 'mk-dash'} />
        );
      })}
      {children}
    </svg>
  );
}
