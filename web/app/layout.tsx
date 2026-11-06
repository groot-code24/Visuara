import type { Metadata, Viewport } from 'next';
import { headers } from 'next/headers';
import { Doto, Funnel_Display, Geist, Geist_Mono } from 'next/font/google';
import './globals.css';
import { Preloader } from '@/components/preloader';
import { SITE_URL } from '@/lib/site';

// Headlines with a machined, confident edge; a neutral face for reading; mono for measurements; dots to echo the mark.
const sans = Geist({ subsets: ['latin'], variable: '--font-body', display: 'swap' });
const display = Funnel_Display({ subsets: ['latin'], variable: '--font-headline', display: 'swap' });
const mono = Geist_Mono({ subsets: ['latin'], variable: '--font-code', display: 'swap' });
const dots = Doto({ subsets: ['latin'], axes: ['ROND'], variable: '--font-dots', display: 'swap' });

const description =
  'Upload your product images and videos. Visuara finds the surfaces in every scene, decides which product fits where, and renders placements that pass a frame-by-frame quality check.';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  openGraph: { type: 'website', siteName: 'Visuara', url: '/', title: 'Visuara: product placement in the videos you already have', description },
  twitter: { card: 'summary_large_image', title: 'Visuara: product placement in the videos you already have', description },
  title: { default: 'Visuara — product placement in the videos you already have', template: '%s · Visuara' },
  description,
  icons: { icon: '/icon.svg' },
};

export const viewport: Viewport = { themeColor: '#ffffff' };

// The intro plays once per browser tab session, on the home page only: a tiny script in <head> marks a repeat visit
// before the first paint, so the page is never covered twice. Storage only, no cookie.
const INTRO_ONCE = "try{if(sessionStorage.getItem('vs-intro'))document.documentElement.classList.add('vs-seen');else sessionStorage.setItem('vs-intro','1')}catch(e){}";

// Every page renders per request: the Content-Security-Policy nonce (proxy.ts) is new each time, and a page prerendered
// at build time could not carry it. Reading the request headers is what makes Next.js render dynamically.
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const h = await headers();
  const path = (h.get('x-pathname') || '').split('?')[0];
  const nonce = /'nonce-([^']+)'/.exec(h.get('content-security-policy') || '')?.[1];
  const intro = path === '/';
  return (
    <html lang="en" className={`${sans.variable} ${display.variable} ${mono.variable} ${dots.variable}`} suppressHydrationWarning>
      <head>{intro && <script nonce={nonce} dangerouslySetInnerHTML={{ __html: INTRO_ONCE }} />}</head>
      <body className="min-h-screen font-sans">{intro && <Preloader />}{children}</body>
    </html>
  );
}
