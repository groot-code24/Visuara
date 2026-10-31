import Link from 'next/link';
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react';

export function cx(...c: (string | false | null | undefined)[]) { return c.filter(Boolean).join(' '); }

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'accent';
type Size = 'sm' | 'md' | 'lg';
const variants: Record<Variant, string> = {
  primary: 'bg-ink text-white hover:bg-ink-2 disabled:bg-ink/40',
  accent: 'bg-accent text-white hover:bg-accent-ink disabled:bg-accent/40',
  secondary: 'bg-card text-ink border border-line-2 hover:bg-soft disabled:text-muted',
  ghost: 'text-ink-2 hover:bg-soft disabled:text-muted',
  danger: 'bg-card text-bad border border-bad/30 hover:bg-bad-soft disabled:opacity-50',
};
const sizes: Record<Size, string> = { sm: 'h-8 px-3 text-[13px] gap-1.5', md: 'h-10 px-4 text-sm gap-2', lg: 'h-12 px-5 text-[15px] gap-2' };

export function buttonClass(variant: Variant = 'primary', size: Size = 'md', extra?: string) {
  return cx('inline-flex items-center justify-center rounded-xl font-medium transition-colors disabled:cursor-not-allowed whitespace-nowrap', variants[variant], sizes[size], extra);
}

export function Button({ variant = 'primary', size = 'md', loading, className, children, disabled, ...p }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; loading?: boolean }) {
  return (
    <button className={buttonClass(variant, size, className)} disabled={disabled || loading} {...p}>
      {loading && <Spinner className="-ml-0.5" />}
      {children}
    </button>
  );
}

export function ButtonLink({ href, variant = 'primary', size = 'md', className, children }: { href: string; variant?: Variant; size?: Size; className?: string; children: ReactNode }) {
  return <Link href={href} className={buttonClass(variant, size, className)}>{children}</Link>;
}

export function Spinner({ className, size = 16 }: { className?: string; size?: number }) {
  return (
    <svg className={cx('animate-spin', className)} width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Input({ className, ...p }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx('h-11 w-full rounded-xl border border-line-2 bg-card px-3.5 text-[15px] text-ink placeholder:text-muted/70 outline-none transition focus:border-ink/40 focus:ring-4 focus:ring-ink/5 disabled:bg-soft', className)} {...p} />;
}

export function Textarea({ className, ...p }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx('w-full rounded-xl border border-line-2 bg-card px-3.5 py-2.5 text-[15px] outline-none transition focus:border-ink/40 focus:ring-4 focus:ring-ink/5', className)} {...p} />;
}

export function Select({ className, children, ...p }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx('h-11 w-full rounded-xl border border-line-2 bg-card px-3 text-[15px] outline-none focus:border-ink/40 focus:ring-4 focus:ring-ink/5', className)} {...p}>{children}</select>;
}

export function Label({ children, htmlFor, hint }: { children: ReactNode; htmlFor?: string; hint?: ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 flex items-baseline justify-between text-[13px] font-medium text-ink-2">
      <span>{children}</span>{hint && <span className="font-normal text-muted">{hint}</span>}
    </label>
  );
}

export function Card({ className, children, ...p }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cx('rounded-2xl border border-line bg-card shadow-card', className)} {...p}>{children}</div>;
}

type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'accent';
const tones: Record<Tone, string> = {
  neutral: 'bg-soft text-ink-2', good: 'bg-good-soft text-good', warn: 'bg-warn-soft text-warn', bad: 'bg-bad-soft text-bad', accent: 'bg-accent-soft text-accent-ink',
};
export function Badge({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={cx('inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium', tones[tone], className)}>{children}</span>;
}

const STATUS: Record<string, [Tone, string]> = {
  DRAFT: ['neutral', 'Draft'], APPROVED: ['accent', 'Approved'], RENDERING: ['warn', 'Rendering'], RENDERED: ['good', 'Rendered'],
  DELIVERED: ['good', 'Delivered'], REJECTED: ['bad', 'Rejected'], RENDER_FAILED: ['bad', 'Render failed'], REVOKED: ['bad', 'Taken down'],
  EXPIRED: ['bad', 'Expired'], CANCELLED: ['neutral', 'Cancelled'], QUEUED: ['neutral', 'Queued'], RUNNING: ['warn', 'Running'],
  SUCCEEDED: ['good', 'Done'], FAILED: ['bad', 'Failed'], DEAD_LETTER: ['bad', 'Failed'], PASSED: ['good', 'Passed'], NOT_EVALUATED: ['neutral', 'Not checked'],
};
export function StatusBadge({ status }: { status: string }) {
  const [tone, label] = STATUS[status] || ['neutral', status.replaceAll('_', ' ').toLowerCase()];
  return <Badge tone={tone}>{label}</Badge>;
}

export function PageHeader({ title, description, actions, back }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; back?: ReactNode }) {
  return (
    <div className="mb-8">
      {back && <div className="mb-3">{back}</div>}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-[34px] leading-tight tracking-tight text-ink">{title}</h1>
          {description && <p className="mt-1.5 max-w-2xl text-[15px] text-muted">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  );
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-2xl border border-dashed border-line-2 bg-card/60 px-6 py-14 text-center">
      {icon && <div className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-soft text-ink-2">{icon}</div>}
      <h3 className="text-base font-semibold text-ink">{title}</h3>
      {children && <p className="mt-1.5 max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Alert({ tone = 'bad', children, className }: { tone?: 'bad' | 'warn' | 'good' | 'neutral'; children: ReactNode; className?: string }) {
  const t = { bad: 'border-bad/20 bg-bad-soft text-bad', warn: 'border-warn/20 bg-warn-soft text-warn', good: 'border-good/20 bg-good-soft text-good', neutral: 'border-line bg-soft text-ink-2' }[tone];
  return <div role={tone === 'bad' ? 'alert' : 'status'} className={cx('rounded-xl border px-3.5 py-2.5 text-sm', t, className)}>{children}</div>;
}

export function Progress({ value, className, striped }: { value: number; className?: string; striped?: boolean }) {
  return (
    <div className={cx('h-2 w-full overflow-hidden rounded-full bg-soft', className)} role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cx('h-full rounded-full bg-accent transition-[width] duration-500', striped && 'cc-stripes')} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function Meter({ label, used, limit, format = (n: number) => String(n) }: { label: string; used: number; limit: number | null; format?: (n: number) => string }) {
  const f = limit ? Math.min(1, used / limit) : 0;
  return (
    <div>
      <div className="mb-1.5 flex justify-between text-[13px]"><span className="text-ink-2">{label}</span><span className="tabular-nums text-muted">{format(used)}{limit ? ` / ${format(limit)}` : ' · unlimited'}</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-soft"><div className={cx('h-full rounded-full', f > .9 ? 'bg-bad' : f > .7 ? 'bg-warn' : 'bg-ink')} style={{ width: `${limit ? Math.max(2, f * 100) : 0}%` }} /></div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) { return <div className={cx('cc-skeleton', className)} />; }
