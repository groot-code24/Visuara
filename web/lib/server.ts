import { cookies } from 'next/headers';
import { SITE_URL } from './site';
import type { Me } from './types';

// The API's private address (a Vercel service binding), then the public site, which routes /api to the same service.
const API_ORIGINS = [...new Set([
  (process.env.CC_API_ORIGIN || (process.env.VERCEL ? '' : 'http://127.0.0.1:8000')).replace(/\/$/, ''),
  process.env.VERCEL ? SITE_URL : '',
])].filter(Boolean);

/** The signed-in user, validated by the API on every request; null when the session is missing, expired or suspended. */
export async function getMe(): Promise<Me | null> {
  const jar = await cookies();
  const session = jar.get('cc_session')?.value;
  if (!session) return null;
  let problem = 'The Visuara API is unreachable.';
  for (const origin of API_ORIGINS) {
    try {
      const r = await fetch(`${origin}/api/me`, { headers: { cookie: `cc_session=${session}` }, cache: 'no-store', redirect: 'error' });
      if (r.status === 401 || r.status === 403) return null;
      if (r.ok) return (await r.json()) as Me;
      problem = `The Visuara API returned ${r.status}.`;
    } catch {
      // try the next address
    }
  }
  throw new Error(problem);
}
