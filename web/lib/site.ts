// Public site settings, set in the hosting environment and fixed at build time.

/** The canonical origin, for metadata and the sitemap. */
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ||
  (process.env.VERCEL_PROJECT_PRODUCTION_URL ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}` : 'http://localhost:3000')
).replace(/\/$/, '');

/** Where sales, upgrade, privacy and support requests go. */
export const CONTACT_EMAIL = process.env.NEXT_PUBLIC_CONTACT_EMAIL || '';

/** A link that starts a conversation with us; sign-in when no contact address is configured. */
export function contactHref(subject: string, fallback = '/login') {
  return CONTACT_EMAIL ? `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(subject)}` : fallback;
}
