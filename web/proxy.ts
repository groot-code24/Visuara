import { NextResponse, type NextRequest } from 'next/server';

/**
 * Auth guard, first line. Requests for the dashboard without a session cookie never reach a dashboard page: they are
 * redirected to /login. The dashboard layout then validates the session with the API on every request (expired or
 * revoked sessions are sent to /login too), and the API enforces auth and workspace roles on every data request.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
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
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // Only pages; /api/* and /auth/google/* are proxied to the API and must not be buffered here.
  matcher: ['/dashboard/:path*'],
};
