// Content-Security-Policy for every page, with a fresh nonce per request (set by proxy.ts). Next.js reads the nonce from
// the request's policy and puts it on its own scripts, so an injected <script> without it never runs. 'strict-dynamic'
// lets those trusted scripts load the chunks they need. Styles stay 'unsafe-inline': React style attributes need it,
// and a style cannot run code.

// Presigned storage URLs are https in production; the local S3 stand-in is plain http on the loopback address.
const storage = process.env.VERCEL ? 'https://*.vercel-storage.com' : 'https: http://127.0.0.1:* http://localhost:*';
const uploads = process.env.VERCEL ? `${storage} https://vercel.com` : storage;   // Vercel Blob's upload API is on vercel.com

export function contentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: ${storage}`,
    `media-src 'self' blob: ${storage}`,
    `connect-src 'self' ${uploads}`,                  // direct uploads go straight to storage
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ');
}

/** A random nonce, base64, 128 bits. */
export function newNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}
