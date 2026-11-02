'use client';

import Link from 'next/link';
import { Button, buttonClass } from '@/components/ui';
import { IconAlert } from '@/components/icons';

/** Catches what the dashboard's own boundary cannot, such as the API being unreachable while its layout checks the session. */
export default function RootError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-line bg-card p-8 text-center shadow-card">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-bad-soft text-bad"><IconAlert /></div>
        <h1 className="mt-4 text-lg font-semibold">We can&apos;t reach Visora right now</h1>
        <p className="mt-2 text-sm text-muted">
          Your work is safe. This is usually brief, so please try again in a moment.{error.digest && <> (ref {error.digest})</>}
        </p>
        <div className="mt-6 flex justify-center gap-2">
          <Button onClick={reset}>Try again</Button>
          <Link href="/" className={buttonClass('secondary')}>Home</Link>
        </div>
      </div>
    </main>
  );
}
