'use client';

import { useEffect, useRef, useState } from 'react';

export function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const m = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(m.matches);
    const on = () => setReduced(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return reduced;
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
  useEffect(() => {
    if (!running) return;
    let raf = 0, last = performance.now(), acc = t, shown = -1;
    const tick = (now: number) => {
      acc = (acc + (now - last) / 1000) % length; last = now;
      const q = Math.floor(acc * 20);
      if (q !== shown) { shown = q; setT(acc); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [running, length]);
  return t;
}
