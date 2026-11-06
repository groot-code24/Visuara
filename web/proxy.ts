import { NextResponse, type NextRequest } from 'next/server';
import { contentSecurityPolicy, newNonce } from '@/lib/csp';

/**
 * Runs before every page.
 *
 * 1. Content-Security-Policy with a fresh nonce (production only: development's hot reload needs eval). Next.js puts the
 *    nonce on its own scripts; any other script is refused by the browser.
 * 2. Auth guard, first line. Requests for the dashboard without a session cookie never reach a dashboard page: they are
 *    redirected to /login. The dashboard layout then validates the session with the API on every request (expired or
 *    revoked sessions are sent to /login too), and the API enforces auth and workspace roles on every data request.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const csp = process.env.NODE_ENV === 'production' ? contentSecurityPolicy(newNonce()) : null;
  const hasSession = Boolean(request.cookies.get('cc_session')?.value);
  if (pathname.startsWith('/dashboard') && !hasSession) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.search = '';
    url.searchParams.set('next', pathname + search);
    return NextResponse.redirect(url);
  }
  const headers = new Headers(request.headers);
  headers.set('x-pathname', pathname + search);              // lets the layout send an expired session back here after sign-in
  if (csp) headers.set('Content-Security-Policy', csp);       // Next.js takes the nonce from here
  const response = NextResponse.next({ request: { headers } });
  if (csp) response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = {
  // Every page. Not /api/* and /auth/google/*: they are proxied to the API and must not be buffered here. Not static
  // files, which carry no script, and not prefetches, which do not render a page.
  matcher: [{
    source: '/((?!api/|auth/google/|_next/static|_next/image|favicon.ico|icon.svg|robots.txt|sitemap.xml|opengraph-image).*)',
    missing: [{ type: 'header', key: 'next-router-prefetch' }, { type: 'header', key: 'purpose', value: 'prefetch' }],
  }],
};
