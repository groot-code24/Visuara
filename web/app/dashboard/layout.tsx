import { redirect } from 'next/navigation';
import { headers } from 'next/headers';
import { getMe } from '@/lib/server';
import { Shell } from './shell';

export const dynamic = 'force-dynamic';

/** Second line of the auth guard: every dashboard request is validated against the API before anything renders. */
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const me = await getMe();
  if (!me) {
    const path = (await headers()).get('x-pathname') || '/dashboard';
    redirect('/login?next=' + encodeURIComponent(path));
  }
  return <Shell me={me}>{children}</Shell>;
}
