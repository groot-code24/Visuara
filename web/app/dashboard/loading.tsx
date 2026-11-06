import { Skeleton } from '@/components/ui';

/** Shown the moment a dashboard link is clicked, while the page is fetched: the menu stays, and the page's outline
 *  appears at once instead of the old page sitting still. Next.js prefetches this for every visible link. */
export default function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading" className="animate-[mk-fade_.2s_ease-out_both]">
      <Skeleton className="h-9 w-56" />
      <Skeleton className="mt-3 h-4 w-80 max-w-full" />
      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        <Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" />
      </div>
      <Skeleton className="mt-6 h-64" />
    </div>
  );
}
