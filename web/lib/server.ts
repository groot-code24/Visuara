import { cookies } from 'next/headers';
import type { Me } from './types';

const API_ORIGIN = (process.env.CC_API_ORIGIN || 'http://127.0.0.1:8000').replace(/\/$/, '');

/** The signed-in user, validated by the API on every request; null when the session is missing, expired or suspended. */
export async function getMe(): Promise<Me | null> {
  const jar = await cookies();
  const session = jar.get('cc_session')?.value;
  if (!session) return null;
  let r: Response;
  try {
    r = await fetch(`${API_ORIGIN}/api/me`, { headers: { cookie: `cc_session=${session}` }, cache: 'no-store' });
  } catch {
    throw new Error('The Visora API is unreachable.');
  }
  if (r.status === 401 || r.status === 403) return null;
  if (!r.ok) throw new Error(`The Visora API returned ${r.status}.`);
  return (await r.json()) as Me;
}
