import type { Metadata, Viewport } from 'next';
import { Inter, Instrument_Serif, JetBrains_Mono } from 'next/font/google';
import './globals.css';
import { SITE_URL } from '@/lib/site';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' });
const serif = Instrument_Serif({ subsets: ['latin'], weight: '400', style: ['normal', 'italic'], variable: '--font-serif', display: 'swap' });
const mono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-jbmono', display: 'swap' });

const description =
  'Upload your product images and videos. Visora finds the surfaces in every scene, decides which product fits where, and renders placements that pass a frame-by-frame quality check.';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  openGraph: { type: 'website', siteName: 'Visora', url: '/', title: 'Visora: product placement in the videos you already have', description },
  twitter: { card: 'summary_large_image', title: 'Visora: product placement in the videos you already have', description },
  title: { default: 'Visora — product placement in the videos you already have', template: '%s · Visora' },
  description,
  icons: { icon: '/icon.svg' },
};

export const viewport: Viewport = { themeColor: '#ffffff' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${serif.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans">{children}</body>
    </html>
  );
}
