import type { Metadata } from 'next';
import { SiteFooter, SiteHeader } from '@/components/site';
import { COMPARISON_ROWS, FAQ, LANDSCAPE } from '@/lib/pricing';
import { PlanCards } from './plans';

export const metadata: Metadata = { title: 'Pricing', description: 'Free, Pro and Agency plans for product placement in your own videos. Under a dollar per rendered video. Start free.' };

export default function Pricing() {
  return (
    <>
      <SiteHeader />
      <main>
        <section className="mx-auto max-w-6xl px-4 pb-16 pt-16 text-center sm:px-6 lg:pt-20">
          <h1 className="mx-auto max-w-3xl font-display text-5xl tracking-tight sm:text-6xl">Priced like a tool, not a production budget</h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-ink-2">
            Every plan includes the decision engine, the frame-by-frame quality check and version-bound approvals. You pay for how many
            decisions and rendered videos you need.
          </p>
          <PlanCards />
        </section>

        <section className="mx-auto max-w-5xl px-4 pb-20 sm:px-6">
          <h2 className="font-display text-3xl tracking-tight">Compare plans</h2>
          <div className="mt-6 overflow-x-auto rounded-2xl border border-line bg-card">
            <table className="w-full min-w-[640px] text-sm">
              <thead><tr className="border-b border-line text-left">
                <th className="px-5 py-3.5 font-medium text-muted">&nbsp;</th>
                {['Free', 'Pro', 'Agency', 'Enterprise'].map((h) => <th key={h} className="px-5 py-3.5 font-semibold">{h}</th>)}
              </tr></thead>
              <tbody>
                {COMPARISON_ROWS.map((r) => (
                  <tr key={r.label} className="border-b border-line last:border-0">
                    <td className="px-5 py-3 text-ink-2">{r.label}</td>
                    {r.values.map((v, i) => <td key={i} className="px-5 py-3 tabular-nums">{v}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="border-y border-line bg-card/60">
          <div className="mx-auto max-w-5xl px-4 py-20 sm:px-6">
            <h2 className="font-display text-3xl tracking-tight">How this compares</h2>
            <p className="mt-3 max-w-3xl text-ink-2">
              Product placement today means a sponsored reshoot, a managed placement network or a generative editor. Every paid plan works out
              under a dollar per rendered video, and the product in the result is exactly your product.
            </p>
            <div className="mt-8 grid gap-4">
              {LANDSCAPE.map((l) => (
                <div key={l.kind} className="grid gap-4 rounded-2xl border border-line bg-card p-5 md:grid-cols-[1.1fr_1.4fr_1.6fr]">
                  <div><p className="font-semibold">{l.kind}</p><p className="mt-1 text-sm text-muted">{l.examples}</p></div>
                  <p className="text-sm text-ink-2">{l.price}</p>
                  <p className="text-sm text-ink-2">{l.difference}</p>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-muted">Competitor prices come from the vendors’ own pricing pages where published (checked 31 October 2026), otherwise from third-party reports. They change often.</p>
          </div>
        </section>

        <section className="mx-auto max-w-3xl px-4 py-20 sm:px-6">
          <h2 className="font-display text-3xl tracking-tight">Billing questions</h2>
          <div className="mt-6 divide-y divide-line border-y border-line">
            {FAQ.slice(-2).concat([
              { q: 'What counts as a decision?', a: 'One run of the decision engine over a set of products and videos, however many videos it includes (up to your plan’s limit).' },
              { q: 'What counts as a rendered video?', a: 'Each render you start this month, except ones you cancel. Re-rendering after an edit counts again.' },
            ]).map((f) => (
              <details key={f.q} className="group py-5">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-medium">{f.q}<span className="text-muted transition group-open:rotate-45">+</span></summary>
                <p className="mt-3 text-[15px] leading-relaxed text-ink-2">{f.a}</p>
              </details>
            ))}
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
