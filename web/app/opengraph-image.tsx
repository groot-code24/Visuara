import { ImageResponse } from 'next/og';

export const alt = 'Visuara: put your products in the videos you already have';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

/** The preview shown when the site is shared. */
export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div style={{
        width: '100%', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: 72, color: '#fff',
        background: 'radial-gradient(60% 80% at 20% 10%, rgba(124,108,255,.55), transparent 70%), radial-gradient(55% 75% at 90% 10%, rgba(45,212,191,.32), transparent 70%), #0a0a12',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', fontSize: 40, fontWeight: 700, letterSpacing: -1 }}>
          <svg width="66" height="52" viewBox="0 0 390 310" fill="#fff" style={{ marginRight: 20 }}><circle cx="24" cy="24" r="24" /><circle cx="101.5" cy="24" r="24" /><circle cx="288" cy="24" r="24" /><circle cx="365.4" cy="24" r="24" /><circle cx="67.7" cy="92.1" r="24" /><circle cx="142.9" cy="92.1" r="24" /><circle cx="245.9" cy="92.1" r="24" /><circle cx="321.2" cy="92.1" r="24" /><circle cx="112.3" cy="158.7" r="24" /><circle cx="194.1" cy="158.7" r="24" /><circle cx="276.6" cy="158.7" r="24" /><circle cx="155.4" cy="225" r="24" /><circle cx="233" cy="225" r="24" /><circle cx="194" cy="285.7" r="24" /></svg>
          Visuara
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', fontSize: 78, fontWeight: 700, lineHeight: 1.06, letterSpacing: -3 }}>Put your products in the videos you already have.</div>
          <div style={{ display: 'flex', marginTop: 28, fontSize: 32, color: 'rgba(255,255,255,.7)' }}>Decides which product fits where, and checks every frame.</div>
        </div>
      </div>
    ),
    size,
  );
}
