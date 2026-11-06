'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';

/** Load JSON from the API. While `poll(data)` is true the data is refreshed every `interval` ms; polling re-arms
 *  whenever new data arrives (for example after an action that starts a render), not only on first load. */
export function useApi<T>(path: string | null, opts: { poll?: (d: T) => boolean; interval?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const pollRef = useRef(opts.poll);
  useEffect(() => { pollRef.current = opts.poll; });

  const reload = useCallback(async () => {
    if (!path) return null;
    try {
      const d = await api<T>(path);
      setData(d); setError(null); return d;
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'Failed to load')); return null;
    } finally { setLoading(false); }
  }, [path]);

  // First load, and again whenever the path changes. A response for a path that is no longer current is dropped,
  // so a slow earlier request can never overwrite the data of the page now shown.
  useEffect(() => {
    if (!path) return;
    let current = true;
    api<T>(path)
      .then((d) => { if (current) { setData(d); setError(null); } })
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
