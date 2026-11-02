import Link from 'next/link';

// The Visuara mark: a V built from dots. The name is never part of the logo in the interface.
export const LOGO_DOTS = [[24, 24], [101.5, 24], [288, 24], [365.4, 24], [67.7, 92.1], [142.9, 92.1], [245.9, 92.1], [321.2, 92.1], [112.3, 158.7], [194.1, 158.7], [276.6, 158.7], [155.4, 225], [233, 225], [194, 285.7]];

export function LogoMark({ size = 26, inverted = false }: { size?: number; inverted?: boolean }) {
  return (
    <svg width={Math.round(size * 390 / 310)} height={size} viewBox="0 0 390 310" fill={inverted ? '#ffffff' : 'currentColor'} aria-hidden>
      {LOGO_DOTS.map(([cx, cy]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="24" />)}
    </svg>
  );
}

export function Logo({ href = '/', size = 26, inverted = false }: { href?: string; size?: number; inverted?: boolean }) {
  return (
    <Link href={href} className={`inline-flex items-center ${inverted ? 'text-white' : 'text-ink'}`} aria-label="Visuara home">
      <LogoMark size={size} inverted={inverted} />
    </Link>
  );
}
