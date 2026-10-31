'use client';

export class ApiError extends Error {
  status: number; code?: string; detail?: unknown;
  constructor(status: number, message: string, code?: string, detail?: unknown) {
    super(message); this.status = status; this.code = code; this.detail = detail;
  }
}

function readCookie(name: string) {
  const hit = document.cookie.split('; ').find((c) => c.startsWith(name + '='));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : '';
}

async function csrfToken() {
  let t = readCookie('cc_csrf');
  if (!t) {
    const r = await fetch('/api/auth/csrf', { credentials: 'same-origin' });
    t = (await r.json()).csrf;
  }
  return t;
}

export function errorMessage(body: unknown, fallback: string): { message: string; code?: string } {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === 'string') return { message: d };
  if (Array.isArray(d) && d[0]?.msg) return { message: String(d[0].msg) };
  if (d && typeof d === 'object' && 'message' in d) return { message: String((d as { message: string }).message), code: (d as { code?: string }).code };
  return { message: fallback };
}

function fallbackMessage(status: number) {
  if (status === 413) return 'That file is too large to upload. Try a smaller one.';
  if (status === 502 || status === 503 || status === 504) return 'Context Commerce is taking too long to respond. Please try again in a moment.';
  return status >= 500 ? 'Something went wrong on our side. Please try again.' : `Request failed (${status})`;
}

type Opts = { method?: string; json?: unknown; form?: FormData; signal?: AbortSignal; redirectOn401?: boolean };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method || (opts.json !== undefined || opts.form ? 'POST' : 'GET');
  const headers: Record<string, string> = {};
  if (method !== 'GET') headers['X-CSRF-Token'] = await csrfToken();
  let body: BodyInit | undefined;
  if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
  else if (opts.form) body = opts.form;
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body, credentials: 'same-origin', signal: opts.signal, cache: 'no-store' });
  } catch {
    throw new ApiError(0, 'Could not reach Context Commerce. Check your connection and try again.');
  }
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    if (res.status === 401 && opts.redirectOn401 !== false && location.pathname.startsWith('/dashboard')) {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
    }
    const { message, code } = errorMessage(data, fallbackMessage(res.status));
    throw new ApiError(res.status, message, code, data);
  }
  return data as T;
}

/** XHR upload with progress events (fetch has no upload progress). */
export function xhrUpload(url: string, method: 'POST' | 'PUT', body: Blob | FormData, headers: Record<string, string>,
  onProgress: (fraction: number) => void, signal?: AbortSignal): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open(method, url);
    Object.entries(headers).forEach(([k, v]) => x.setRequestHeader(k, v));
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let b: unknown = x.responseText;
      try { b = JSON.parse(x.responseText); } catch { /* not JSON */ }
      resolve({ status: x.status, body: b });
    };
    x.onerror = () => reject(new ApiError(0, 'Network error during upload. Check your connection and retry.'));
    x.onabort = () => reject(new ApiError(0, 'Upload cancelled'));
    signal?.addEventListener('abort', () => x.abort());
    x.send(body);
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Large files take a while to process, and a gateway may give up first while the API carries on. Asking again is safe. */
async function completeUpload(id: number, signal?: AbortSignal) {
  const deadline = Date.now() + 5 * 60_000;
  for (;;) {
    try {
      return await api<{ id: number }>(`/api/uploads/${id}/complete`, { method: 'POST', signal });
    } catch (e) {
      const transient = e instanceof ApiError && !signal?.aborted && ([0, 502, 503, 504].includes(e.status) || (e.status === 409 && e.message.includes('being processed')));
      if (!transient || Date.now() > deadline) throw e;
      await sleep(3000);
    }
  }
}

/** Run a placement's quality check. A long one answers RUNNING while it carries on, so keep asking until it reports. */
export async function runQualityCheck(placementId: number) {
  for (;;) {
    const r = await api<{ status: string }>(`/api/placements/${placementId}/quality-check`, { method: 'POST' });
    if (r.status !== 'RUNNING') return r;
    await sleep(2000);
  }
}

/** Upload one video: straight to private storage when the server offers it, otherwise through the API. */
export async function uploadVideo(file: File, title: string, onProgress: (f: number) => void, signal?: AbortSignal) {
  const type = file.type || 'video/mp4';
  const init = await api<{ method: string; upload_id?: number; put_url?: string; headers?: Record<string, string> }>('/api/uploads/video', {
    json: { title, filename: file.name, content_type: type, size_bytes: file.size, license_confirmed: true },
  });
  if (init.method === 'direct' && init.put_url && init.upload_id) {
    const put = await xhrUpload(init.put_url, 'PUT', file, init.headers || { 'Content-Type': type }, onProgress, signal);
    if (put.status < 200 || put.status >= 300) throw new ApiError(put.status, 'Storage refused the upload. Try again.');
    return completeUpload(init.upload_id, signal);
  }
  const form = new FormData();
  form.append('title', title); form.append('license_confirmed', 'true'); form.append('video', file);
  const r = await xhrUpload('/api/videos', 'POST', form, { 'X-CSRF-Token': await csrfToken() }, onProgress, signal);
  if (r.status < 200 || r.status >= 300) {
    const e = errorMessage(r.body, 'Upload failed');
    throw new ApiError(r.status, e.message, e.code);
  }
  return r.body as { id: number };
}
