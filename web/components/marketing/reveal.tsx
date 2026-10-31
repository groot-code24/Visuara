'use client';

import { useEffect, useRef, type ReactNode } from 'react';
import { cx } from '@/components/ui';

/** Fades its children up the first time they scroll into view. */
export function Reveal({ children, className, delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { el.classList.add('is-in'); io.disconnect(); } }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return <div ref={ref} className={cx('mk-reveal', className)} style={delay ? { transitionDelay: `${delay}ms` } : undefined}>{children}</div>;
}
