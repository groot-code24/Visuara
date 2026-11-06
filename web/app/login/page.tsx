import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { AuthShell } from '@/components/auth-shell';
import { SignInAside } from '@/components/auth-aside';
import { getMe } from '@/lib/server';
import { LoginForm } from './login-form';
import { safeNext } from '@/lib/format';

export const metadata: Metadata = { title: 'Sign in' };

export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const me = await getMe().catch(() => null);
  if (me) redirect(safeNext(sp.next));
  return (
    <AuthShell aside={<SignInAside />}>
      <LoginForm next={safeNext(sp.next)} error={sp.error} invited={sp.invited === '1'} plan={sp.plan} />
    </AuthShell>
  );
}
