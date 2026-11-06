import type { CSSProperties } from 'react';
import { LOGO_DOTS } from './logo';

// Scattered dots gather into the mark, on the first visit to the home page in a browser session (app/layout.tsx). CSS only, so it plays from the first paint, needs no
// JavaScript and can never block the page: it ignores clicks, removes itself, and is skipped for reduced motion.
function dotStyle(i: number) {
  const angle = i * 2.399963;              // the golden angle spreads the starting points evenly
  const distance = 620 + (i * 173) % 700;
  return {
    '--from-x': `${Math.round(Math.cos(angle) * distance)}px`,
    '--from-y': `${Math.round(Math.sin(angle) * distance)}px`,
    '--delay': `${i * 16}ms`,
  } as CSSProperties;
}

export function Preloader() {
  return (
    <div className="vs-loader" aria-hidden>
      <svg width="124" height="99" viewBox="0 0 390 310" fill="currentColor" overflow="visible">
        {LOGO_DOTS.map(([cx, cy], i) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="24" style={dotStyle(i)} />)}
      </svg>
    </div>
  );
}
