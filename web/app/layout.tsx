import type { Metadata, Viewport } from 'next';
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${display.variable} ${mono.variable} ${dots.variable}`}>
      <body className="min-h-screen font-sans"><Preloader />{children}</body>
    </html>
  );
}
