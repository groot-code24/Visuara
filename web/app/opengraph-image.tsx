import { ImageResponse } from 'next/og';

export const alt = 'Context Commerce: put your products in the videos you already have';
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
        <div style={{ display: 'flex', fontSize: 34, fontWeight: 700, letterSpacing: -1 }}>Context Commerce</div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', fontSize: 78, fontWeight: 700, lineHeight: 1.06, letterSpacing: -3 }}>Put your products in the videos you already have.</div>
          <div style={{ display: 'flex', marginTop: 28, fontSize: 32, color: 'rgba(255,255,255,.7)' }}>Decides which product fits where, and checks every frame.</div>
        </div>
      </div>
    ),
    size,
  );
}
