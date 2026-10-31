'use client';

import { Button, Card } from '@/components/ui';
import { IconAlert } from '@/components/icons';

export default function DashboardError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="flex min-h-[70vh] items-center justify-center p-4">
      <Card className="max-w-md p-8 text-center">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-bad-soft text-bad"><IconAlert /></div>
        <h1 className="mt-4 text-lg font-semibold">Something went wrong</h1>
        <p className="mt-2 text-sm text-muted">{error.message || 'The page could not be loaded.'}{error.digest && <> (ref {error.digest})</>}</p>
        <Button className="mt-6" onClick={reset}>Try again</Button>
      </Card>
    </div>
  );
}
