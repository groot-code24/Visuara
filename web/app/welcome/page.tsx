'use client';

import { useEffect, useMemo, useState } from 'react';
import { AuthShell } from '@/components/auth-shell';
import { Alert, Button, Input, Label, Spinner, cx } from '@/components/ui';
import { IconArrowLeft, IconArrowRight, IconBox, IconCheck, IconFilm, IconShield, IconSpark, IconUsers } from '@/components/icons';
import { api, ApiError } from '@/lib/api';
import { ANNUAL_SAVING, PLANS } from '@/lib/pricing';
import { contactHref } from '@/lib/site';

type Kind = 'direct' | 'brand' | 'publisher' | 'agency';
const KINDS: { id: Kind; title: string; detail: string; can: string[]; Icon: typeof IconBox }[] = [
  { id: 'brand', title: 'A brand', Icon: IconBox, detail: 'Put your products into scenes from publishers’ videos.',
    can: ['Browse publishers’ scenes', 'Run campaigns and request placements', 'Pay invoices online'] },
  { id: 'publisher', title: 'A publisher or studio', Icon: IconFilm, detail: 'Turn the scenes in your videos into paid inventory.',
    can: ['Analyse your videos', 'Offer scenes to brands', 'Approve requests and get paid'] },
  { id: 'agency', title: 'An agency', Icon: IconUsers, detail: 'Run placement campaigns for the brands you represent.',
    can: ['Work inside client workspaces', 'Invite clients as viewers', 'Manager approval before renders'] },
  { id: 'direct', title: 'Just my own videos', Icon: IconSpark, detail: 'Place your own products in your own footage.',
    can: ['Upload products and videos', 'Get ranked placement decisions', 'Render and download'] },
];

type PaidPlan = 'pro' | 'team';
type PlanId = 'free' | PaidPlan;
type Interval = 'monthly' | 'annual';
type Pending = { email: string; name: string; provider: string; suggested_workspace: string; invitations: { id: number; member_role: string; organization: string }[] };
type Billing = { ready: boolean; mode: 'test' | 'live' | null; plans: { id: PaidPlan; available: Record<Interval, boolean> }[] };
type Step = 'you' | 'workspace' | 'plan';

function wantedPlan(): PlanId {
  let v: string | null = null;
  try { v = new URLSearchParams(window.location.search).get('plan') || window.localStorage.getItem('visuara.plan'); } catch { /* storage blocked */ }
  return v === 'pro' || v === 'team' ? v : 'free';
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

  const buyable = (id: PlanId, i: Interval = period) => id === 'free' || !!billing.plans.find((x) => x.id === id)?.available[i];
  const plan = buyable(wanted) ? wanted : 'free';        // a plan that cannot be bought for this period falls back to Free
  const creating = choice === 'new';
  const steps: Step[] = creating ? ['you', 'workspace', 'plan'] : ['you'];
  const index = steps.indexOf(step);
  const selected = PLANS.find((x) => x.id === plan)!;
  const price = period === 'annual' ? selected.annual : selected.monthly;
  const paying = plan !== 'free' && buyable(plan);
  const canNext = step === 'you' ? !!name.trim() : step === 'workspace' ? !!org.trim() : true;

  const finalLabel = useMemo(() => {
    if (!creating) return 'Join workspace';
    if (!paying) return 'Create workspace';
    return `Create workspace and pay $${(period === 'annual' ? (price ?? 0) * 12 : price ?? 0).toLocaleString('en-US')}${period === 'annual' ? ' a year' : ' a month'}`;
  }, [creating, paying, period, price]);

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
    if (!canNext) return;
    if (index < steps.length - 1) { setStep(steps[index + 1]); setError(null); } else void submit();
  }

  async function cancel() {
    await api('/api/auth/signup/cancel', { method: 'POST' }).catch(() => {});
    window.location.assign('/login');
  }

  if (!p) return <AuthShell><div className="flex justify-center py-16 text-muted"><Spinner size={24} /></div></AuthShell>;

  const titles: Record<Step, [string, string]> = {
    you: ["Let's set you up", `Signing up as ${p.email}`],
    workspace: ['Your workspace', 'Everything you make lives in a workspace you can share with your team.'],
    plan: ['Choose your plan', 'Start free, or pick a paid plan now. You can change it any time in Settings.'],
  };

  return (
    <AuthShell wide>
      <div className="mx-auto max-w-[560px] text-center">
        {steps.length > 1 && <p className="mk-eyebrow justify-center">Step {index + 1} of {steps.length}</p>}
        <h1 className="mt-3 font-display text-[40px] leading-tight tracking-tight">{titles[step][0]}</h1>
        <p className="mt-2 text-[15px] text-muted">{titles[step][1]}</p>
        {steps.length > 1 && (
          <ol className="mt-6 grid grid-cols-3 gap-2" aria-label="Progress">
            {steps.map((s, i) => (
              <li key={s} className={cx('h-1.5 rounded-full transition-colors', i <= index ? 'bg-ink' : 'bg-line')} aria-current={i === index ? 'step' : undefined}>
                <span className="sr-only">{titles[s][0]}</span>
              </li>
            ))}
          </ol>
        )}
      </div>

      <form onSubmit={next} className="mt-8 rounded-3xl border border-line bg-card p-6 shadow-card sm:p-8">
        {error && <Alert className="mb-5">{error}</Alert>}

        {step === 'you' && (
          <div className="mx-auto max-w-[440px]">
            <Label htmlFor="name">Your name</Label>
            <Input id="name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="Alex Morgan" />
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
            <div className="mx-auto max-w-[440px]">
              <Label htmlFor="org" hint="Your company, brand or studio">Workspace name</Label>
              <Input id="org" required maxLength={120} value={org} onChange={(e) => setOrg(e.target.value)} autoFocus />
            </div>
            <fieldset className="mt-7">
              <legend className="mb-3 text-sm font-medium">What will you use Visuara for?</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                {KINDS.map(({ id, title, detail, can, Icon }) => {
                  const on = kind === id;
                  return (
                    <label key={id} className={cx('relative flex cursor-pointer flex-col rounded-2xl border p-4 transition',
                      on ? 'border-ink bg-soft ring-1 ring-ink' : 'border-line-2 hover:border-ink/40 hover:bg-soft/60')}>
                      <input type="radio" name="kind" value={id} checked={on} onChange={() => setKind(id)} className="sr-only" />
                      <div className="flex items-center gap-3">
                        <span className={cx('grid h-9 w-9 shrink-0 place-items-center rounded-xl', on ? 'bg-ink text-white' : 'bg-accent-soft text-accent-ink')}><Icon size={18} /></span>
                        <span className="font-medium">{title}</span>
                        <span className={cx('ml-auto grid h-5 w-5 shrink-0 place-items-center rounded-full border', on ? 'border-ink bg-ink text-white' : 'border-line-2')}>
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
              <div className="inline-flex rounded-xl border border-line bg-canvas p-1 text-sm" role="group" aria-label="Billing period">
                {(['monthly', 'annual'] as const).map((v) => (
                  <button key={v} type="button" onClick={() => setPeriod(v)} aria-pressed={period === v}
                    className={cx('rounded-lg px-4 py-1.5 font-medium transition', period === v ? 'bg-ink text-white' : 'text-ink-2 hover:bg-soft')}>
                    {v === 'monthly' ? 'Monthly' : `Annual · save ${ANNUAL_SAVING}`}
                  </button>
                ))}
              </div>
              {billing.ready && billing.mode === 'test' && <span className="rounded-full bg-warn-soft px-2.5 py-1 text-xs font-medium text-warn">Test mode: no real charge</span>}
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              {PLANS.filter((x) => x.id !== 'enterprise').map((x) => {
                const id = x.id as PlanId; const on = plan === id; const ok = buyable(id);
                const amount = period === 'annual' ? x.annual : x.monthly;
                return (
                  <button key={x.id} type="button" disabled={!ok} onClick={() => setPlan(id)} aria-pressed={on}
                    className={cx('relative flex flex-col rounded-2xl border p-4 text-left transition disabled:cursor-not-allowed disabled:opacity-55',
                      on ? 'border-ink bg-soft ring-1 ring-ink' : 'border-line-2 enabled:hover:border-ink/40 enabled:hover:bg-soft/60')}>
                    {x.badge && <span className="absolute -top-2.5 left-4 rounded-full bg-gradient-to-r from-[#7c6cff] to-trust px-2 py-0.5 text-[11px] font-semibold text-white">{x.badge}</span>}
                    <span className="flex items-center justify-between font-medium">{x.name}
                      <span className={cx('grid h-5 w-5 place-items-center rounded-full border', on ? 'border-ink bg-ink text-white' : 'border-line-2')}>{on && <IconCheck size={12} />}</span>
                    </span>
                    <span className="mt-2"><span className="font-display text-3xl tabular-nums">${amount}</span><span className="text-sm text-muted"> / month</span></span>
                    <span className="text-xs text-muted">{amount === 0 ? 'no card needed' : period === 'annual' ? `billed $${((amount ?? 0) * 12).toLocaleString('en-US')} yearly` : 'billed monthly'}</span>
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

        <div className={cx('mt-8 flex items-center gap-3', index > 0 ? 'justify-between' : 'justify-end')}>
          {index > 0 && (
            <Button type="button" variant="ghost" onClick={() => setStep(steps[index - 1])}><IconArrowLeft size={16} /> Back</Button>
          )}
          <Button type="submit" size="lg" className={cx(index === 0 && 'w-full sm:w-auto sm:min-w-[220px]')} loading={busy} disabled={!canNext}>
            {index < steps.length - 1 ? <>Continue <IconArrowRight size={16} /></> : finalLabel}
          </Button>
        </div>
      </form>
      <button onClick={cancel} className="mx-auto mt-5 block text-sm text-muted hover:text-ink">Cancel and sign out</button>
    </AuthShell>
  );
}

function Choice({ on, onClick, title, detail }: { on: boolean; onClick: () => void; title: string; detail: string }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on}
      className={cx('flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left transition', on ? 'border-ink bg-soft ring-1 ring-ink' : 'border-line-2 hover:bg-soft')}>
      <span className="min-w-0 flex-1"><span className="block font-medium">{title}</span><span className="text-sm text-muted">{detail}</span></span>
      <span className={cx('grid h-5 w-5 shrink-0 place-items-center rounded-full border', on ? 'border-ink bg-ink text-white' : 'border-line-2')}>{on && <IconCheck size={12} />}</span>
    </button>
  );
}
