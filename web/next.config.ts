import type { NextConfig } from 'next';

// The FastAPI backend. The browser only ever talks to this app's origin: /api/* and the Google OAuth callback are
// routed to it, so the session cookie is first-party and the auth guard (proxy.ts) can see it. On Vercel the root
// vercel.json does that routing; everywhere else these rewrites do.
const API_ORIGIN = (process.env.CC_API_ORIGIN || 'http://127.0.0.1:8000').replace(/\/$/, '');

if (process.env.VERCEL && !process.env.NEXT_PUBLIC_CONTACT_EMAIL) console.warn('Warning: NEXT_PUBLIC_CONTACT_EMAIL is not set, so "Talk to us" and upgrade links have nowhere to go.');

// Presigned storage URLs are https in production; the local S3 stand-in is plain http on the loopback address.
const storage = process.env.VERCEL ? 'https:' : 'https: http://127.0.0.1:* http://localhost:*';
const contentSecurityPolicy = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",                        // Next.js inlines its bootstrap; pages are static, so no nonce
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${storage}`,
  `media-src 'self' blob: ${storage}`,
  `connect-src 'self' ${storage}`,                            // direct uploads go straight to the bucket
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=()' },
  ...(process.env.NODE_ENV === 'production' ? [
    { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' },
    { key: 'Content-Security-Policy', value: contentSecurityPolicy },
  ] : []),
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  allowedDevOrigins: ['localhost', '127.0.0.1'],
  output: process.env.CC_STANDALONE === '1' ? 'standalone' : undefined,
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    if (process.env.VERCEL) return [];
    return [
      { source: '/api/:path*', destination: `${API_ORIGIN}/api/:path*` },
      { source: '/auth/google/:path*', destination: `${API_ORIGIN}/auth/google/:path*` },
    ];
  },
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

export default nextConfig;
