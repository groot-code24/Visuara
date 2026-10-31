'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Logo, LogoMark } from './logo';
import { cx } from './ui';
import { IconMenu, IconSpark, IconUser, IconX } from './icons';
import { CONTACT_EMAIL } from '@/lib/site';

const NAV = [
  { href: '/#demo', label: 'Product' },
  { href: '/#agencies', label: 'Agencies' },
  { href: '/pricing', label: 'Pricing' },
  { href: '/#faq', label: 'FAQ' },
];

/** A black "notch" hanging from the top edge, the same on dark and light pages. */
export function SiteHeader() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on(); window.addEventListener('scroll', on, { passive: true });
    return () => window.removeEventListener('scroll', on);
  }, []);
  return (
    <header className="pointer-events-none sticky top-0 z-40 h-16">
      <div className={cx('pointer-events-auto mx-2 overflow-hidden rounded-b-[22px] border border-t-0 border-white/10 bg-black text-white transition-shadow duration-300 sm:mx-auto sm:max-w-[920px]',
        scrolled ? 'shadow-[0_18px_50px_-12px_rgba(10,10,18,.55)]' : 'shadow-[0_8px_30px_-16px_rgba(10,10,18,.5)]')}>
        <div className="flex h-16 items-center justify-between gap-3 pl-3.5 pr-2 sm:pl-6 sm:pr-2.5">
          <Link href="/" className="flex shrink-0 items-center gap-2.5" aria-label="Context Commerce home">
            <LogoMark size={28} inverted />
            <span className="text-[15px] font-bold tracking-[-0.03em] sm:text-[17px]">Context Commerce<span className="text-brand">.</span></span>
          </Link>
          <nav className="hidden items-center gap-1 md:flex" aria-label="Main">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} className="rounded-full px-3.5 py-2 text-[15px] font-medium text-white/75 transition hover:bg-white/10 hover:text-white">{n.label}</Link>
            ))}
          </nav>
          <div className="flex shrink-0 items-center gap-2">
            <Link href="/login" aria-label="Sign in" title="Sign in"
              className="hidden h-11 w-11 place-items-center rounded-full bg-white/[.09] text-white/85 transition hover:bg-white/15 hover:text-white sm:grid">
              <IconUser size={18} />
            </Link>
            <Link href="/login" className="inline-flex h-10 items-center gap-2 rounded-full bg-white px-3.5 text-[14px] font-semibold text-black transition hover:bg-white/90 max-[359px]:hidden sm:h-11 sm:pl-3.5 sm:pr-4 sm:text-[15px]">
              <IconSpark size={17} className="hidden sm:block" />Start free
            </Link>
            <button onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? 'Close menu' : 'Open menu'}
              className="grid h-10 w-10 place-items-center rounded-full text-white/85 hover:bg-white/10 md:hidden">
              {open ? <IconX /> : <IconMenu />}
            </button>
          </div>
        </div>
        {open && (
          <nav className="border-t border-white/10 px-3 pb-3 pt-2 md:hidden" aria-label="Mobile">
            {[...NAV, { href: '/login', label: 'Sign in' }].map((n) => (
              <Link key={n.href + n.label} href={n.href} onClick={() => setOpen(false)} className="block rounded-xl px-3 py-3 text-[15px] text-white/85 hover:bg-white/10">{n.label}</Link>
            ))}
          </nav>
        )}
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-14 text-sm sm:px-6 md:grid-cols-[1.4fr_1fr_1fr]">
        <div>
          <Logo />
          <p className="mt-3 max-w-xs text-muted">The decision engine for product placement in the videos you already have.</p>
        </div>
        <div>
          <p className="font-medium">Product</p>
          <ul className="mt-3 space-y-2 text-muted">
            <li><Link href="/#demo" className="hover:text-ink">How it decides</Link></li>
            <li><Link href="/#agencies" className="hover:text-ink">For agencies</Link></li>
            <li><Link href="/pricing" className="hover:text-ink">Pricing</Link></li>
          </ul>
        </div>
        <div>
          <p className="font-medium">Get started</p>
          <ul className="mt-3 space-y-2 text-muted">
            <li><Link href="/login" className="hover:text-ink">Create a free workspace</Link></li>
            <li><Link href="/login" className="hover:text-ink">Sign in</Link></li>
            <li><Link href="/#faq" className="hover:text-ink">FAQ</Link></li>
          </ul>
        </div>
      </div>
      <div className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-5 text-xs text-muted sm:px-6">
          <p>© {new Date().getFullYear()} Context Commerce · Now in pilot</p>
          <div className="flex gap-4">
            <Link href="/terms" className="hover:text-ink">Terms</Link>
            <Link href="/privacy" className="hover:text-ink">Privacy</Link>
            {CONTACT_EMAIL && <a href={`mailto:${CONTACT_EMAIL}`} className="hover:text-ink">Contact</a>}
          </div>
        </div>
      </div>
    </footer>
  );
}
