'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Input, Label, Skeleton, cx } from '@/components/ui';
import { LogoMark } from '@/components/logo';
import { IconArrowLeft, IconArrowRight, IconBox, IconCheck, IconFilm, IconShield, IconSpark, IconUsers } from '@/components/icons';
import type { ProductId } from '@/components/marketing/scene';
import { api, ApiError } from '@/lib/api';
import { ANNUAL_SAVING, PLANS } from '@/lib/pricing';
import { contactHref } from '@/lib/site';
import { Preview, type Kind, type Step } from './preview';

const KINDS: { id: Kind; title: string; detail: string; can: string[]; Icon: typeof IconBox }[] = [
  { id: 'brand', title: 'A brand', Icon: IconBox, detail: 'Get your product into scenes audiences already watch.',
    can: ['Search publishers’ scenes by fit', 'Campaigns with approvals built in', 'Pay per placement, online'] },
  { id: 'publisher', title: 'A publisher or studio', Icon: IconFilm, detail: 'Turn your back catalogue into sellable inventory.',
    can: ['Analyse videos into placement-ready scenes', 'Set prices and brand-safety rules', 'Approve requests and get paid'] },
  { id: 'agency', title: 'An agency', Icon: IconUsers, detail: 'Run placements for every brand you represent.',
    can: ['Work inside client workspaces', 'Invite clients to review', 'Manager sign-off before any render'] },
  { id: 'direct', title: 'Just my own videos', Icon: IconSpark, detail: 'Put your own products into your own footage.',
    can: ['Upload products and videos', 'A ranked decision on what fits where', 'Check, render and download'] },
];

type PaidPlan = 'pro' | 'team';
type PlanId = 'free' | PaidPlan;
type Interval = 'monthly' | 'annual';
type Pending = { email: string; name: string; provider: string; suggested_workspace: string; invitations: { id: number; member_role: string; organization: string }[] };
type Billing = { ready: boolean; mode: 'test' | 'live' | null; plans: { id: PaidPlan; available: Record<Interval, boolean> }[] };
const STEP_LABEL: Record<Step, string> = { you: 'You', workspace: 'Workspace', plan: 'Plan' };
const PAID = PLANS.filter((x) => x.id !== 'enterprise');
const money = (n: number) => `$${n.toLocaleString('en-US')}`;

function wantedPlan(): PlanId {
  let v: string | null = null;
  try { v = new URLSearchParams(window.location.search).get('plan') || window.localStorage.getItem('visuara.plan'); } catch { /* storage blocked */ }
  return v === 'pro' || v === 'team' ? v : 'free';
}

function typing(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
}

export default function WelcomePage() {
  const [p, setP] = useState<Pending | null>(null);
  const [billing, setBilling] = useState<Billing>({ ready: false, mode: null, plans: [] });
  const [step, setStep] = useState<Step>('you');
  const [name, setName] = useState('');
  const [org, setOrg] = useState('');
  const [choice, setChoice] = useState<number | 'new'>('new');
  const [kind, setKind] = useState<Kind>('direct');
  const [wanted, setPlan] = useState<PlanId>('free');
  const [period, setPeriod] = useState<Interval>('annual');
  const [product, setProduct] = useState<ProductId>('aura');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Pending>('/api/auth/signup', { redirectOn401: false })
      .then((d) => {
        setP(d); setName(d.name); setOrg(d.suggested_workspace);
        if (d.invitations.length) setChoice(d.invitations[0].id);
      })
      .catch(() => window.location.replace('/dashboard'));   // no sign-up in progress: signed in already, or back to /login via the guard
    api<Billing>('/api/billing/plans', { redirectOn401: false }).then((bl) => { setBilling(bl); setPlan(wantedPlan()); }).catch(() => {});
  }, []);

  const buyable = useCallback((id: PlanId, i: Interval = period) => id === 'free' || !!billing.plans.find((x) => x.id === id)?.available[i], [billing, period]);
  const plan = buyable(wanted) ? wanted : 'free';        // a plan that cannot be bought for this period falls back to Free
  const creating = choice === 'new';
  const steps: Step[] = creating ? ['you', 'workspace', 'plan'] : ['you'];
  const index = steps.indexOf(step);
  const selected = PLANS.find((x) => x.id === plan)!;
  const price = (period === 'annual' ? selected.annual : selected.monthly) ?? 0;
  const paying = plan !== 'free' && buyable(plan);
  const canNext = step === 'you' ? !!name.trim() : step === 'workspace' ? !!org.trim() : true;

  const finalLabel = useMemo(() => {
    if (!creating) return 'Join workspace';
    if (!paying) return 'Create workspace';
    return `Create workspace and pay ${money(period === 'annual' ? price * 12 : price)}${period === 'annual' ? ' a year' : ' a month'}`;
  }, [creating, paying, period, price]);

  // Keyboard: 1 to 4 choose the workspace type, 1 to 3 the plan (when not typing in a field). Enter continues.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (typing(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      const n = Number(e.key);
      if (step === 'workspace' && n >= 1 && n <= KINDS.length) setKind(KINDS[n - 1].id);
      if (step === 'plan' && n >= 1 && n <= PAID.length && buyable(PAID[n - 1].id as PlanId)) setPlan(PAID[n - 1].id as PlanId);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, buyable]);

  async function submit() {
    setBusy(true); setError(null);
    try {
      await api('/api/auth/signup', { json: creating ? { name, organization: org, kind } : { name, invitation_id: choice } });
      try { window.localStorage.removeItem('visuara.plan'); } catch { /* storage blocked */ }
      if (creating && paying) {
        try {
          const r = await api<{ checkout_url: string }>('/api/billing/checkout', { json: { plan, interval: period } });
          window.location.assign(r.checkout_url); return;
        } catch {
          window.location.assign('/dashboard/settings?billing=retry#plan'); return;   // the workspace exists; pay from Settings
        }
      }
      window.location.assign('/dashboard');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not finish setting up your account.'); setBusy(false);
    }
  }

  function next(e: React.FormEvent) {
    e.preventDefault();
    if (!canNext || busy) return;
    if (index < steps.length - 1) { setStep(steps[index + 1]); setError(null); } else void submit();
  }

  async function cancel() {
    await api('/api/auth/signup/cancel', { method: 'POST' }).catch(() => {});
    window.location.assign('/login');
  }

  const titles: Record<Step, [string, string]> = {
    you: ["Let's set you up", p ? `Two minutes from here to your first placement. You’re signing up as ${p.email}.` : ''],
    workspace: ['Your workspace', 'Your team, footage and placements live here. Tell us how you’ll use Visuara and we’ll shape the workspace around it.'],
    plan: ['Choose your plan', 'Start free and upgrade when Visuara pays for itself. Switch or cancel any time.'],
  };
  const planInfo = { name: selected.name, price: price ? `${money(price)}/mo` : '$0', renders: selected.renders ? `${selected.renders} rendered videos a month` : 'Custom volume' };

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(420px,0.82fr)]">
      <div className="flex min-h-screen flex-col px-5 sm:px-10 xl:px-16">
        <header className="flex h-16 items-center justify-between">
          <Link href="/" className="inline-flex items-center" aria-label="Visuara home"><LogoMark /></Link>
          <button onClick={cancel} className="text-sm text-muted transition hover:text-ink">Cancel and sign out</button>
        </header>

        <main className="mx-auto flex w-full max-w-[620px] flex-1 flex-col justify-center py-10">
          {!p ? (
            <div aria-busy="true"><Skeleton className="h-4 w-40" /><Skeleton className="mt-4 h-11 w-80 max-w-full" /><Skeleton className="mt-3 h-4 w-96 max-w-full" /><Skeleton className="mt-8 h-40" /></div>
          ) : (
            <>
              {steps.length > 1 && (
                <ol className="mb-8 flex items-center gap-2 text-sm" aria-label="Progress">
                  {steps.map((s, i) => (
                    <li key={s} className="flex items-center gap-2">
                      <button type="button" disabled={i >= index} onClick={() => setStep(s)} aria-current={i === index ? 'step' : undefined}
                        className={cx('flex items-center gap-2 rounded-full border px-3 py-1 transition',
                          i === index ? 'border-ink bg-ink text-white' : i < index ? 'border-line-2 bg-card text-ink hover:border-ink' : 'border-line text-muted')}>
                        <span className={cx('grid h-4 w-4 place-items-center rounded-full text-[10px]', i < index && 'bg-good text-white')}>{i < index ? <IconCheck size={10} /> : i + 1}</span>
                        {STEP_LABEL[s]}
                      </button>
                      {i < steps.length - 1 && <span className={cx('h-px w-6 sm:w-10', i < index ? 'bg-ink' : 'bg-line-2')} />}
                    </li>
                  ))}
                </ol>
              )}

              <div key={step} className="ob-step">
                <p className="mk-eyebrow">{step === 'you' ? 'Welcome to Visuara' : `Step ${index + 1} of ${steps.length}`}</p>
                <h1 className="mt-3 font-display text-[40px] leading-[1.05] tracking-tight sm:text-[48px]">{titles[step][0]}</h1>
                <p className="mt-3 max-w-[540px] text-[15px] leading-relaxed text-muted">{titles[step][1]}</p>

                <form onSubmit={next} className="mt-8">
                  {error && <Alert className="mb-5">{error}</Alert>}

                  {step === 'you' && (
                    <div className="max-w-[440px]">
                      <Label htmlFor="name">Your name</Label>
                      <Input id="name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="Alex Morgan" className="h-12 text-base" />
                      {p.invitations.length > 0 && (
                        <div className="mt-6">
                          <Label>Workspace</Label>
                          <div className="space-y-2">
                            {p.invitations.map((i) => (
                              <Choice key={i.id} on={choice === i.id} onClick={() => setChoice(i.id)} title={`Join ${i.organization}`} detail={`You were invited as ${i.member_role}`} />
                            ))}
                            <Choice on={choice === 'new'} onClick={() => setChoice('new')} title="Create a new workspace" detail="For your own company, brand or studio" />
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {step === 'workspace' && (
                    <>
                      <div className="max-w-[440px]">
                        <Label htmlFor="org" hint="Your company, brand or studio">Workspace name</Label>
                        <Input id="org" required maxLength={120} value={org} onChange={(e) => setOrg(e.target.value)} autoFocus className="h-12 text-base" />
                      </div>
                      <fieldset className="mt-7">
                        <legend className="mb-3 flex w-full items-center justify-between text-sm font-medium">
                          What will you use Visuara for?<span className="hidden text-xs font-normal text-muted sm:inline">Press 1–4</span>
                        </legend>
                        <div className="grid gap-3 sm:grid-cols-2">
                          {KINDS.map(({ id, title, detail, can, Icon }, n) => {
                            const on = kind === id;
                            return (
                              <label key={id} className={cx('group relative flex cursor-pointer flex-col rounded-2xl border bg-card p-4 transition duration-200',
                                on ? 'border-ink shadow-[0_0_0_1px_var(--color-ink),0_12px_32px_-16px_rgba(24,24,27,.35)]' : 'border-line-2 hover:-translate-y-0.5 hover:border-ink/40 hover:shadow-card')}>
                                <input type="radio" name="kind" value={id} checked={on} onChange={() => setKind(id)} className="sr-only" />
                                <div className="flex items-center gap-3">
                                  <span className={cx('grid h-9 w-9 shrink-0 place-items-center rounded-xl transition-colors', on ? 'bg-ink text-white' : 'bg-accent-soft text-accent-ink')}><Icon size={18} /></span>
                                  <span className="font-medium">{title}</span>
                                  <kbd className="ml-auto hidden rounded border border-line px-1.5 font-mono text-[10px] text-muted sm:inline">{n + 1}</kbd>
                                  <span className={cx('grid h-5 w-5 shrink-0 place-items-center rounded-full border transition', on ? 'border-ink bg-ink text-white' : 'border-line-2')}>
                                    {on && <IconCheck size={12} />}
                                  </span>
                                </div>
                                <span className="mt-2 text-sm text-muted">{detail}</span>
                                <ul className="mt-3 space-y-1 text-[13px] text-ink-2">
                                  {can.map((c) => <li key={c} className="flex gap-1.5"><IconCheck size={14} className="mt-0.5 shrink-0 text-good" />{c}</li>)}
                                </ul>
                              </label>
                            );
                          })}
                        </div>
                        <p className="mt-3 text-xs text-muted">The owner can change this later in Organizations, until the workspace has marketplace activity.</p>
                      </fieldset>
                    </>
                  )}

                  {step === 'plan' && (
                    <>
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="inline-flex rounded-xl border border-line bg-card p-1 text-sm" role="group" aria-label="Billing period">
                          {(['monthly', 'annual'] as const).map((v) => (
                            <button key={v} type="button" onClick={() => setPeriod(v)} aria-pressed={period === v}
                              className={cx('rounded-lg px-4 py-1.5 font-medium transition', period === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-soft')}>
                              {v === 'monthly' ? 'Monthly' : `Annual · save ${ANNUAL_SAVING}`}
                            </button>
                          ))}
                        </div>
                        <span className="flex items-center gap-3">
                          {billing.ready && billing.mode === 'test' && <span className="rounded-full bg-warn-soft px-2.5 py-1 text-xs font-medium text-warn">Test mode: no real charge</span>}
                          <span className="hidden text-xs text-muted sm:inline">Press 1–3</span>
                        </span>
                      </div>
                      <div className="mt-5 grid gap-3 sm:grid-cols-3">
                        {PAID.map((x) => {
                          const id = x.id as PlanId; const on = plan === id; const ok = buyable(id);
                          const amount = (period === 'annual' ? x.annual : x.monthly) ?? 0;
                          return (
                            <button key={x.id} type="button" disabled={!ok} onClick={() => setPlan(id)} aria-pressed={on}
                              className={cx('relative flex flex-col rounded-2xl border bg-card p-4 text-left transition duration-200 disabled:cursor-not-allowed disabled:opacity-55',
                                on ? 'border-ink shadow-[0_0_0_1px_var(--color-ink),0_12px_32px_-16px_rgba(24,24,27,.35)]' : 'border-line-2 enabled:hover:-translate-y-0.5 enabled:hover:border-ink/40 enabled:hover:shadow-card')}>
                              {x.badge && <span className="absolute -top-2.5 left-4 rounded-full bg-gradient-to-r from-[#7c6cff] to-trust px-2 py-0.5 text-[11px] font-semibold text-white">{x.badge}</span>}
                              <span className="flex items-center justify-between font-medium">{x.name}
                                <span className={cx('grid h-5 w-5 place-items-center rounded-full border', on ? 'border-ink bg-ink text-white' : 'border-line-2')}>{on && <IconCheck size={12} />}</span>
                              </span>
                              <span className="mt-2"><span className="font-display text-3xl tabular-nums">{money(amount)}</span><span className="text-sm text-muted"> / month</span></span>
                              <span className="text-xs text-muted">{amount === 0 ? 'no card needed' : period === 'annual' ? `billed ${money(amount * 12)} yearly` : 'billed monthly'}</span>
                              <ul className="mt-3 space-y-1 text-[13px] text-ink-2">
                                {x.features.slice(0, 4).map((f) => <li key={f} className="flex gap-1.5"><IconCheck size={14} className="mt-0.5 shrink-0 text-good" />{f}</li>)}
                              </ul>
                              {!ok && <span className="mt-3 text-xs text-muted">Not available online yet: start on Free and upgrade later.</span>}
                            </button>
                          );
                        })}
                      </div>
                      <p className="mt-4 flex items-start gap-2 text-xs text-muted">
                        <IconShield size={14} className="mt-0.5 shrink-0" />
                        <span>
                          {paying
                            ? 'Payment is handled by Lemon Squeezy, our merchant of record: your card never reaches Visuara. Cancel, change card or download invoices any time from Settings.'
                            : 'The Free plan needs no card. Upgrade from Settings whenever you are ready.'}
                          {' '}Need more volume? <a className="underline" href={contactHref('Visuara Enterprise', '/pricing')}>Talk to us about Enterprise</a>.
                        </span>
                      </p>
                    </>
                  )}

                  <div className="mt-9 flex flex-wrap items-center gap-3">
                    {index > 0 && (
                      <Button type="button" variant="ghost" onClick={() => setStep(steps[index - 1])}><IconArrowLeft size={16} /> Back</Button>
                    )}
                    <Button type="submit" size="lg" className="min-w-[200px]" loading={busy} disabled={!canNext}>
                      {index < steps.length - 1 ? <>Continue <IconArrowRight size={16} /></> : finalLabel}
                    </Button>
                    <span className="hidden text-xs text-muted sm:inline">or press <kbd className="rounded border border-line px-1.5 font-mono">Enter ↵</kbd></span>
                  </div>
                </form>
              </div>
            </>
          )}
        </main>

        <footer className="pb-6 text-xs text-muted">
          © Visuara · <Link href="/terms" className="hover:text-ink">Terms</Link> · <Link href="/privacy" className="hover:text-ink">Privacy</Link>
        </footer>
      </div>

      <aside className="mk-dark hidden lg:block">
        <div className="sticky top-0 h-screen"><Preview step={step} name={name} org={org} kind={creating ? kind : 'direct'} plan={planInfo} product={product} onProduct={setProduct} /></div>
      </aside>
    </div>
  );
}

function Choice({ on, onClick, title, detail }: { on: boolean; onClick: () => void; title: string; detail: string }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on}
      className={cx('flex w-full items-center gap-3 rounded-xl border bg-card px-4 py-3 text-left transition', on ? 'border-ink shadow-[0_0_0_1px_var(--color-ink)]' : 'border-line-2 hover:bg-soft')}>
      <span className="min-w-0 flex-1"><span className="block font-medium">{title}</span><span className="text-sm text-muted">{detail}</span></span>
      <span className={cx('grid h-5 w-5 shrink-0 place-items-center rounded-full border', on ? 'border-ink bg-ink text-white' : 'border-line-2')}>{on && <IconCheck size={12} />}</span>
    </button>
  );
}
