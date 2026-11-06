import Link from 'next/link';
import type { ReactNode } from 'react';
import { LogoMark } from './logo';

export function AuthShell({ children, wide, aside }: { children: ReactNode; wide?: boolean; aside?: ReactNode }) {
  if (aside) {
    return (
      <div className="min-h-screen lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(440px,0.9fr)]">
        <div className="flex min-h-screen flex-col">
          <header className="flex h-16 items-center px-5 sm:px-8">
            <Link href="/" className="inline-flex items-center" aria-label="Visuara home"><LogoMark /></Link>
          </header>
          <main className="flex flex-1 items-center justify-center px-4 pb-16 pt-6">
            <div className="ob-step w-full max-w-[400px]">{children}</div>
          </main>
          <footer className="pb-6 text-center text-xs text-muted">
            © Visuara · <Link href="/terms" className="hover:text-ink">Terms</Link> · <Link href="/privacy" className="hover:text-ink">Privacy</Link>
          </footer>
        </div>
        <aside className="mk-dark hidden lg:block"><div className="sticky top-0 h-screen">{aside}</div></aside>
      </div>
    );
  }
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-16 items-center px-5 sm:px-8">
        <Link href="/" className="inline-flex items-center" aria-label="Visuara home"><LogoMark /></Link>
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pb-16 pt-[8vh]">
        <div className={wide ? 'w-full max-w-[760px]' : 'w-full max-w-[400px]'}>{children}</div>
      </main>
      <footer className="pb-6 text-center text-xs text-muted">
        © Visuara · <Link href="/terms" className="hover:text-ink">Terms</Link> · <Link href="/privacy" className="hover:text-ink">Privacy</Link>
      </footer>
    </div>
  );
}
