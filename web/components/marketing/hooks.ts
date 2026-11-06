'use client';

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';
function subscribeReducedMotion(onChange: () => void) {
  const m = window.matchMedia(REDUCED_MOTION);
  m.addEventListener('change', onChange);
  return () => m.removeEventListener('change', onChange);
}

/** The visitor's reduced-motion setting; false while rendering on the server. */
export function useReducedMotion() {
  return useSyncExternalStore(subscribeReducedMotion, () => window.matchMedia(REDUCED_MOTION).matches, () => false);
}

/** True while the element is on screen (so animations pause when scrolled away); with `once`, stays true after the first time. */
export function useInView<T extends Element>(threshold = 0.2, once = false) {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      setInView(e.isIntersecting || (once && e.boundingClientRect.top < 0));
      if (once && e.isIntersecting) io.disconnect();
    }, { threshold });
    io.observe(el);
    return () => io.disconnect();
  }, [threshold, once]);
  return [ref, inView] as const;
}

/** A looping clock in seconds, ticking about 20 times a second while `running`. */
export function useClock(length: number, running: boolean) {
  const [t, setT] = useState(length * 0.45);
  const at = useRef(t);            // where the clock stopped, so it resumes there rather than restarting
  useEffect(() => {
    if (!running) return;
    let raf = 0, last = performance.now(), shown = -1;
    const tick = (now: number) => {
      at.current = (at.current + (now - last) / 1000) % length; last = now;
      const q = Math.floor(at.current * 20);
      if (q !== shown) { shown = q; setT(at.current); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [running, length]);
  return t;
}
