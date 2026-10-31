'use client';

import { useId, useState } from 'react';
import Link from 'next/link';
import { PLANS } from '@/lib/pricing';
import { contactHref } from '@/lib/site';

const money = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: n < 10 ? 2 : 0 });

function Slider({ label, value, min, max, step = 1, onChange, format }: {
  label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void; format: (v: number) => string;
}) {
  const id = useId();
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium text-ink-2">{label}</label>
        <span className="font-display text-2xl tabular-nums">{format(value)}</span>
      </div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 h-2 w-full cursor-pointer appearance-none rounded-full accent-[#5b4fe9]"
        style={{ background: `linear-gradient(90deg, #5b4fe9 ${pct}%, #e7e5e0 ${pct}%)` }} />
    </div>
  );
}

/** Agency estimate from the visitor's own numbers: which plan covers their volume and what each placement costs. */
export function AgencyCalculator() {
  const [clients, setClients] = useState(6);
  const [perClient, setPerClient] = useState(4);
  const [today, setToday] = useState(600);
  const placements = clients * perClient;
  const plan = PLANS.find((p) => p.renders != null && p.id !== 'free' && p.renders >= placements) ?? PLANS[PLANS.length - 1];
  const cost = plan.annual;
  const current = placements * today;
  return (
    <div className="grid overflow-hidden rounded-3xl border border-line bg-card shadow-card lg:grid-cols-[1.1fr_1fr]">
      <div className="space-y-7 p-6 sm:p-8">
        <div>
          <p className="text-sm font-medium text-accent-ink">Estimate it for your agency</p>
          <p className="mt-1 text-sm text-muted">Move the sliders to match your book of business.</p>
        </div>
        <Slider label="Clients with video" value={clients} min={1} max={40} onChange={setClients} format={(v) => String(v)} />
        <Slider label="Placements per client per month" value={perClient} min={1} max={20} onChange={setPerClient} format={(v) => String(v)} />
        <Slider label="What one sponsored integration costs you today" value={today} min={50} max={3000} step={50} onChange={setToday} format={money} />
      </div>
      <div className="mk-dark relative flex flex-col justify-between gap-6 overflow-hidden p-6 sm:p-8">
        <div className="mk-glow pointer-events-none absolute inset-0 opacity-70" />
        <div className="relative">
          <p className="text-[11px] font-medium uppercase tracking-[.14em] text-white/50">Your month</p>
          <p className="mt-2 font-display text-5xl tabular-nums">{placements} <span className="text-2xl text-white/60">placements</span></p>
          <p className="mt-1 text-sm text-white/60">{clients} clients × {perClient} each</p>
        </div>
        <dl className="relative grid grid-cols-2 gap-4 text-sm">
          <div className="rounded-2xl bg-white/[.06] p-4">
            <dt className="text-white/55">Plan that covers it</dt>
            <dd className="mt-1 text-lg font-semibold">{plan.name}</dd>
            <dd className="text-white/55">{cost == null ? 'custom volume' : `${money(cost)}/month, billed yearly`}</dd>
          </div>
          <div className="rounded-2xl bg-white/[.06] p-4">
            <dt className="text-white/55">Per placement</dt>
            <dd className="mt-1 text-lg font-semibold tabular-nums">{cost == null ? '—' : money(cost / placements)}</dd>
            <dd className="text-white/55">vs {money(today)} today</dd>
          </div>
          <div className="col-span-2 rounded-2xl bg-gradient-to-r from-[#7c6cff]/30 to-trust/20 p-4 ring-1 ring-white/10">
            <dt className="text-white/70">The same volume at today’s cost</dt>
            <dd className="mt-1 font-display text-3xl tabular-nums">{money(current)}<span className="text-base text-white/60"> /month</span></dd>
          </div>
        </dl>
        <p className="relative text-[11px] leading-relaxed text-white/45">
          An estimate from your inputs, not a quote. It leaves out your team’s review time. Every render you start counts toward the allowance, including re-renders after edits.
          {cost == null && <> Above 200 placements a month, <Link href={contactHref('Context Commerce Enterprise', '/login?plan=enterprise')} className="underline">talk to us</Link>.</>}
        </p>
      </div>
    </div>
  );
}
