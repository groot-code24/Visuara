'use client';

import { useRef, useState } from 'react';
import { Scene } from './scene';

/** Before/after: the same frame with and without the placement. Drag, click or use the arrow keys. */
export function BeforeAfter() {
  const [pos, setPos] = useState(55);
  const box = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const move = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    setPos(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  };
  return (
    <figure>
      <div ref={box} className="relative aspect-video cursor-ew-resize touch-pan-y select-none overflow-hidden rounded-3xl border border-line bg-black shadow-pop has-[input:focus-visible]:ring-4 has-[input:focus-visible]:ring-accent/40"
        onPointerDown={(e) => { dragging.current = true; e.currentTarget.setPointerCapture(e.pointerId); move(e.clientX); }}
        onPointerMove={(e) => dragging.current && move(e.clientX)}
        onPointerUp={() => { dragging.current = false; }}>
        <div className="absolute inset-0"><Scene placed={{ product: 'north', surface: 'poster' }} /></div>
        <div className="absolute inset-0" style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}><Scene /></div>
        <span className="absolute left-3 top-3 rounded-full bg-black/60 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur">Original footage</span>
        <span className="absolute right-3 top-3 rounded-full bg-[#7c6cff] px-2.5 py-1 text-[11px] font-medium text-white">With placement</span>
        <div className="pointer-events-none absolute inset-y-0" style={{ left: `${pos}%` }}>
          <div className="absolute inset-y-0 -left-px w-0.5 bg-white shadow-[0_0_12px_rgba(0,0,0,.4)]" />
          <div className="absolute left-1/2 top-1/2 grid h-10 w-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-white text-ink shadow-pop">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><path d="m9 6-6 6 6 6M15 6l6 6-6 6" /></svg>
          </div>
        </div>
        <input type="range" min={0} max={100} value={Math.round(pos)} onChange={(e) => setPos(Number(e.target.value))}
          aria-label="Compare original and placed footage" className="sr-only" />
      </div>
      <figcaption className="mt-3 text-center text-xs text-muted">Illustration. Drag to compare: the rest of the frame is untouched, and the product is composited, not regenerated.</figcaption>
    </figure>
  );
}
