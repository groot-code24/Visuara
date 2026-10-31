'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { createContext, useContext, useEffect, useState } from 'react';
import type { Me } from '@/lib/types';
import { LogoMark } from '@/components/logo';
import { ToastProvider } from '@/components/toast';
import { cx } from '@/components/ui';
import { IconBox, IconDownload, IconHome, IconLayers, IconLogout, IconMenu, IconSettings, IconSpark, IconTarget, IconX } from '@/components/icons';
import { api } from '@/lib/api';

const MeCtx = createContext<Me | null>(null);
export const useMe = () => useContext(MeCtx)!;
const RANK = { viewer: 0, editor: 1, manager: 2, admin: 3, owner: 4 } as const;
export const can = (me: Me, role: keyof typeof RANK) => RANK[me.member_role] >= RANK[role];

const NAV = [
  { href: '/dashboard', label: 'Overview', icon: IconHome, exact: true },
  { href: '/dashboard/decide', label: 'New decision', icon: IconSpark },
  { href: '/dashboard/decisions', label: 'Decisions', icon: IconTarget },
  { href: '/dashboard/library', label: 'Library', icon: IconBox },
  { href: '/dashboard/placements', label: 'Placements', icon: IconLayers },
  { href: '/dashboard/renders', label: 'Renders', icon: IconDownload },
  { href: '/dashboard/settings', label: 'Settings', icon: IconSettings },
];

async function signOut() {
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  window.location.assign('/login');
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav className="space-y-0.5">
      {NAV.map(({ href, label, icon: Icon, exact }) => {
        const active = exact ? path === href : path.startsWith(href);
        return (
          <Link key={href} href={href} onClick={onNavigate}
            className={cx('flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition', active ? 'bg-card font-medium text-ink shadow-card' : 'text-ink-2 hover:bg-card/70')}>
            <Icon size={17} className={active ? 'text-accent' : 'text-muted'} />{label}
          </Link>
        );
      })}
    </nav>
  );
}

function Account({ me }: { me: Me }) {
  return (
    <div className="rounded-2xl border border-line bg-card p-3">
      <div className="flex items-center gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent-ink">
          {(me.name || me.email).slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{me.name}</p>
          <p className="truncate text-xs text-muted">{me.organization} · {me.plan_name}</p>
        </div>
        <button onClick={signOut} title="Sign out" aria-label="Sign out" className="rounded-lg p-1.5 text-muted hover:bg-soft hover:text-ink"><IconLogout size={17} /></button>
      </div>
    </div>
  );
}

export function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);
  return (
    <MeCtx.Provider value={me}>
      <ToastProvider>
        <div className="min-h-screen lg:grid lg:grid-cols-[256px_1fr]">
          <aside className="hidden h-screen flex-col justify-between border-r border-line bg-soft/60 px-4 py-5 lg:sticky lg:top-0 lg:flex">
            <div>
              <Link href="/dashboard" className="mb-7 flex items-center gap-2.5 px-2 font-semibold tracking-tight"><LogoMark /><span className="text-[15px]">Context Commerce</span></Link>
              <Nav />
            </div>
            <Account me={me} />
          </aside>

          <div className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-canvas/90 px-4 backdrop-blur lg:hidden">
            <Link href="/dashboard" className="flex items-center gap-2 font-semibold"><LogoMark size={24} /><span className="text-sm">Context Commerce</span></Link>
            <button onClick={() => setOpen(true)} aria-label="Open menu" className="rounded-lg p-2 hover:bg-soft"><IconMenu /></button>
          </div>
          {open && (
            <div className="fixed inset-0 z-40 lg:hidden">
              <div className="absolute inset-0 bg-ink/30" onClick={() => setOpen(false)} />
              <div className="absolute inset-y-0 left-0 flex w-72 flex-col justify-between bg-canvas p-4 shadow-pop">
                <div>
                  <div className="mb-6 flex items-center justify-between px-2"><span className="font-semibold">Menu</span>
                    <button onClick={() => setOpen(false)} aria-label="Close menu" className="rounded-lg p-1.5 hover:bg-soft"><IconX /></button></div>
                  <Nav onNavigate={() => setOpen(false)} />
                </div>
                <Account me={me} />
              </div>
            </div>
          )}

          <main className="min-w-0 px-4 py-8 sm:px-8 lg:px-12 lg:py-10">
            <div className="mx-auto max-w-6xl">{children}</div>
          </main>
        </div>
      </ToastProvider>
    </MeCtx.Provider>
  );
}
