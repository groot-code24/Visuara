'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';

/* A small stale-while-revalidate cache shared by every page in this browser tab.
 *
 * Every round trip to the API crosses an ocean for most visitors, so the app never makes people wait twice for the
 * same thing: a page shows what it last saw at once and refreshes it in the background, identical requests made at
 * the same moment share one fetch, and links can warm a page's data before it is clicked (`prefetchApi`).
 * The cache lives only in memory: it is gone on a full page load, which is also what signing out and switching
 * organization do, so one person's data is never shown to another. Nothing is cached on the server. */
type Entry = { data?: unknown; at: number; inflight?: Promise<unknown> };
const cache: Map<string, Entry> | null = typeof window === 'undefined' ? null : new Map();
const FRESH_MS = 4000;                  // a prefetch this recent is reused instead of fetching again on mount

function load<T>(path: string, force = false): Promise<T> {
  const e = cache?.get(path);
  if (e?.inflight && !force) return e.inflight as Promise<T>;
  if (!force && e && e.data !== undefined && Date.now() - e.at < FRESH_MS) return Promise.resolve(e.data as T);
  const p = api<T>(path).then(
    (d) => { cache?.set(path, { data: d, at: Date.now() }); return d; },
    (err) => { const prev = cache?.get(path); if (prev) { delete prev.inflight; } throw err; },
  );
  cache?.set(path, { ...(e || { at: 0 }), inflight: p });
  return p;
}

/** Start loading these API paths now (on hover or focus of a link), so the page has its data when it opens. */
export function prefetchApi(paths: readonly (string | null | undefined)[]) {
  if (!cache) return;
  for (const p of paths) if (p) load(p).catch(() => {});
}

/** Forget cached responses whose path starts with one of these prefixes (after a change made elsewhere). */
export function invalidateApi(...prefixes: string[]) {
  if (!cache) return;
  for (const k of [...cache.keys()]) if (prefixes.some((p) => k.startsWith(p))) cache.delete(k);
}

/** Load JSON from the API. While `poll(data)` is true the data is refreshed every `interval` ms; polling re-arms
 *  whenever new data arrives (for example after an action that starts a render), not only on first load.
 *  A path seen before in this tab renders its last data immediately and is refreshed in the background. */
export function useApi<T>(path: string | null, opts: { poll?: (d: T) => boolean; interval?: number } = {}) {
  // State remembers which path its data belongs to; a path seen before shows its cached data at once (no effect needed).
  const [state, setState] = useState<{ path: string | null; data: T | null }>(() => ({ path, data: (path ? (cache?.get(path)?.data as T | undefined) : undefined) ?? null }));
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(Boolean(path) && state.data === null);
  const pollRef = useRef(opts.poll);
  useEffect(() => { pollRef.current = opts.poll; });
  const hit = state.path !== path && path ? (cache?.get(path)?.data as T | undefined) : undefined;
  const data = state.path === path ? state.data : hit ?? state.data;     // otherwise keep showing the previous data

  const setData = useCallback((d: T | null) => {
    if (path && d !== null) cache?.set(path, { data: d, at: Date.now() });
    setState({ path, data: d });
  }, [path]);

  const reload = useCallback(async () => {
    if (!path) return null;
    try {
      const d = await load<T>(path, true);
      setState({ path, data: d }); setError(null); return d;
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'Failed to load')); return null;
    } finally { setLoading(false); }
  }, [path]);

  // First load, and again whenever the path changes. A response for a path that is no longer current is dropped,
  // so a slow earlier request can never overwrite the data of the page now shown.
  useEffect(() => {
    if (!path) return;
    let current = true;
    load<T>(path)
      .then((d) => { if (current) { setState({ path, data: d }); setError(null); } })
      .catch((e) => { if (current) setError(e instanceof ApiError ? e : new ApiError(0, 'Failed to load')); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [path]);

  useEffect(() => {
    if (!data || !pollRef.current?.(data)) return;
    const t = setTimeout(reload, opts.interval || 2000);
    return () => clearTimeout(t);
  }, [data, reload, opts.interval]);

  return { data, error, loading, reload, setData };
}
