import Link from 'next/link';

export function LogoMark({ size = 28, inverted = false }: { size?: number; inverted?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill={inverted ? '#ffffff' : '#18181b'} />
      <path d="M9 21.5 15 9l2.6 5.4" stroke={inverted ? '#0c0b10' : '#fff'} strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="16.5" y="15.5" width="7.5" height="7.5" rx="1.6" fill={inverted ? '#7c6cff' : '#8b82f6'} />
    </svg>
  );
}

export function Logo({ href = '/', size = 28, inverted = false }: { href?: string; size?: number; inverted?: boolean }) {
  return (
    <Link href={href} className={`inline-flex items-center gap-2.5 font-semibold tracking-tight ${inverted ? 'text-white' : 'text-ink'}`} aria-label="Context Commerce home">
      <LogoMark size={size} inverted={inverted} />
      <span className="text-[15px]">Context Commerce</span>
    </Link>
  );
}
