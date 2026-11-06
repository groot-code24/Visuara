'use client';

import { useState } from 'react';
import Link from 'next/link';
import { cx } from '@/components/ui';
import { IconCheck } from '@/components/icons';
import { ANNUAL_SAVING, PLANS, perRender } from '@/lib/pricing';
import { contactHref } from '@/lib/site';

export function PlanCards() {
  const [annual, setAnnual] = useState(true);
  return (
    <>
      <div className="mt-8 inline-flex rounded-xl border border-line bg-card p-1 text-sm" role="group" aria-label="Billing period">
        {([['Monthly', false], [`Annual · save ${ANNUAL_SAVING}`, true]] as const).map(([label, v]) => (
          <button key={label} onClick={() => setAnnual(v)} aria-pressed={annual === v}
            className={cx('rounded-lg px-4 py-1.5 font-medium transition', annual === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-soft')}>
            {label}
          </button>
        ))}
      </div>
      <div className="mt-10 grid gap-5 text-left md:grid-cols-2 lg:grid-cols-4">
        {PLANS.map((p) => {
          const price = annual ? p.annual : p.monthly;
          const unit = perRender(p, annual);
          const star = !!p.badge;
          return (
            <div key={p.id} className={cx('relative flex flex-col rounded-3xl border p-6 transition hover:-translate-y-0.5',
              star ? 'mk-dark border-transparent shadow-pop lg:-my-3 lg:py-9' : 'border-line bg-card hover:shadow-card')}>
              {star && <div className="mk-glow pointer-events-none absolute inset-0 rounded-3xl opacity-80" />}
              {p.badge && <span className="absolute -top-3 left-6 rounded-full bg-gradient-to-r from-[#7c6cff] to-trust px-3 py-1 text-xs font-semibold text-white shadow">{p.badge}</span>}
              <div className="relative flex flex-1 flex-col">
                <p className="font-semibold">{p.name}</p>
                <div className="mt-3 min-h-[64px]">
                  {price === null ? <span className="font-display text-5xl">Custom</span> : (
                    <>
                      <span className="font-display text-5xl tabular-nums">${price}</span><span className={cx('text-sm', star ? 'text-white/60' : 'text-muted')}> / month</span>
                      <p className={cx('text-xs', star ? 'text-white/55' : 'text-muted')}>{price === 0 ? 'no card needed' : annual ? `billed $${(price * 12).toLocaleString('en-US')} yearly` : 'billed monthly'}</p>
                    </>
                  )}
                </div>
                <p className={cx('mt-2 min-h-10 text-sm', star ? 'text-white/70' : 'text-muted')}>{p.tagline}</p>
                {unit && price !== 0 && (
                  <p className={cx('mt-3 inline-flex w-fit rounded-full px-2.5 py-1 text-xs font-medium', star ? 'bg-white/10 text-white' : 'bg-accent-soft text-accent-ink')}>{unit} per rendered video</p>
                )}
                <Link href={p.id === 'enterprise' ? contactHref('Visuara Enterprise', '/login?plan=enterprise') : `/login?plan=${p.id}`}
                  className={cx('mt-5 inline-flex h-11 w-full items-center justify-center rounded-xl text-sm font-medium transition',
                    star ? 'bg-white text-[#0c0b10] hover:bg-white/85' : p.id === 'pro' ? 'bg-ink text-white hover:bg-ink-2' : 'border border-line-2 bg-card text-ink hover:bg-soft')}>
                  {p.cta}
                </Link>
                <ul className="mt-6 space-y-2.5 text-sm">
                  {p.features.map((f) => <li key={f} className="flex gap-2"><IconCheck size={16} className={cx('mt-0.5 shrink-0', star ? 'text-[#b9b0ff]' : 'text-good')} />{f}</li>)}
                </ul>
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-6 text-center text-xs text-muted">Prices in USD. Pay securely through Lemon Squeezy, our merchant of record (sales tax and VAT included at checkout). Cancel any time.</p>
    </>
  );
}
