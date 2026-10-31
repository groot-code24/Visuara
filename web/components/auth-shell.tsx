import Link from 'next/link';
import type { ReactNode } from 'react';
import { LogoMark } from './logo';

export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-16 items-center px-5 sm:px-8">
        <Link href="/" className="inline-flex items-center gap-2.5 font-semibold tracking-tight"><LogoMark /><span className="text-[15px]">Context Commerce</span></Link>
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pb-16 pt-[8vh]">
        <div className="w-full max-w-[400px]">{children}</div>
      </main>
      <footer className="pb-6 text-center text-xs text-muted">
        © Context Commerce · <Link href="/terms" className="hover:text-ink">Terms</Link> · <Link href="/privacy" className="hover:text-ink">Privacy</Link>
      </footer>
    </div>
  );
}
