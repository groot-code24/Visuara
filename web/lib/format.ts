export const CATEGORIES: { id: string; label: string }[] = [
  { id: 'beverage', label: 'Beverage' }, { id: 'food', label: 'Food & snacks' }, { id: 'beauty', label: 'Beauty & personal care' },
  { id: 'fashion', label: 'Fashion & accessories' }, { id: 'tech', label: 'Tech & electronics' }, { id: 'home', label: 'Home & living' },
  { id: 'fitness', label: 'Sports & fitness' }, { id: 'other', label: 'Other' },
];
export const categoryLabel = (id: string) => CATEGORIES.find((c) => c.id === id)?.label || 'Other';

export function seconds(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toFixed(1).padStart(4, '0')}`;
}
export function duration(s: number) {
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}
export function bytes(n: number) {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
export function when(iso: string | null | undefined) {
  if (!iso) return '';
  const d = new Date(iso.replace(' ', 'T') + (iso.includes('Z') || iso.includes('+') ? '' : 'Z'));
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
}
export const pct = (x: number) => `${Math.round(x * 100)}`;

export const COMPONENT_LABELS: Record<string, string> = {
  screen_time: 'Screen time', size: 'On-screen size', fill: 'Shape fit', stability: 'Tracking stability', contrast: 'Visibility', context: 'Scene context',
};

export const ROLE_HELP: Record<string, string> = {
  owner: 'Everything, including transferring ownership',
  admin: 'Manages the team, plus everything a manager can do',
  manager: 'Approves placements, renders and downloads',
  editor: 'Uploads, runs decisions and prepares placements',
  viewer: 'Read-only',
};

/**
 * Only same-site paths are allowed as post-sign-in destinations (no open redirects).
 * The path is resolved the way a browser resolves it: browsers drop tabs and newlines and treat `\` as `/`, so
 * `/\t/evil.example` would otherwise become `//evil.example`. The resolved path is returned, never the input,
 * and a path that resolves to `//…` (for example `/.//evil.example`) is refused.
 */
export function safeNext(next: string | undefined) {
  const fallback = '/dashboard';
  if (!next || !next.startsWith('/') || next.length > 2048) return fallback;
  const base = 'https://visuara.invalid';
  try {
    const url = new URL(next, base);
    if (url.origin !== base || url.pathname.startsWith('//')) return fallback;
    return url.pathname + url.search + url.hash;
  } catch {
    return fallback;
  }
}

export function money(minor: number | null | undefined, currency: string | null | undefined) {
  if (minor == null || !currency) return '—';
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(minor / 100); }
  catch { return `${(minor / 100).toFixed(2)} ${currency}`; }
}
export const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
