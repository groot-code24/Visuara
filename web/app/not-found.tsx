import { AuthShell } from '@/components/auth-shell';
import { ButtonLink } from '@/components/ui';

export default function NotFound() {
  return (
    <AuthShell>
      <div className="text-center">
        <p className="font-display text-7xl text-line-2">404</p>
        <h1 className="mt-4 font-display text-4xl tracking-tight">Page not found</h1>
        <p className="mt-2 text-muted">The page you are looking for does not exist or was moved.</p>
        <ButtonLink href="/" className="mt-8">Back home</ButtonLink>
      </div>
    </AuthShell>
  );
}
