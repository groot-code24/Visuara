import Link from 'next/link';
import { SiteFooter, SiteHeader } from '@/components/site';
import { IconArrowRight, IconBox, IconCheck, IconFilm, IconLayers, IconShield, IconSpark, IconTarget, IconUsers, IconX } from '@/components/icons';
import { SceneLab } from '@/components/marketing/scene-lab';
import { BeforeAfter } from '@/components/marketing/compare';
import { AgencyCalculator } from '@/components/marketing/roi';
import { FailureList, FrameTrace, LibraryMatrix, VersionTrail } from '@/components/marketing/bento';
import { Reveal } from '@/components/marketing/reveal';
import { PlanCards } from './pricing/plans';
import { FAQ } from '@/lib/pricing';

const MARQUEE = ['Posters', 'Screens', 'Tabletops', 'Signs', 'Packaging faces', 'Every frame checked', 'Never redrawn', 'Approved before render', 'Signed downloads'];

const OPTIONS = [
  {
    title: 'Reshoot the integration', tone: 'muted',
    points: [['Talent, crew and location booked again', false], ['Weeks from brief to delivery', false], ['The product looks exactly right', true]],
  },
  {
    title: 'Ask a generative editor', tone: 'muted',
    points: [['Fast and cheap per clip', true], ['The product is regenerated, so labels and logos can drift', false], ['No check that it holds up on every frame', false]],
  },
  {
    title: 'Visora', tone: 'ours',
    points: [['Minutes, on footage you already own', true], ['Your exact product image, composited, never redrawn', true], ['Measured on every frame, approved before render', true]],
  },
] as const;

const STEPS = [
  { icon: <IconBox />, title: 'Add 2–4 products', body: 'Product images with a category each. Cut-out PNGs look best.' },
  { icon: <IconFilm />, title: 'Add your videos', body: 'The footage you own. Large files upload straight to private storage.' },
  { icon: <IconSpark />, title: 'Get the decision', body: 'For each video: which product, which surface, which seconds, and why.' },
  { icon: <IconLayers />, title: 'Approve and render', body: 'Checked frame by frame, approved by a manager, rendered to MP4.' },
];

const AGENCY = [
  { icon: <IconFilm />, title: 'Sell inventory you already own', body: 'Every client’s back catalogue becomes sponsorable. Add a new line item without a production budget.' },
  { icon: <IconSpark />, title: 'Pitch with a preview, not a deck', body: 'Run a decision on a prospect’s own footage and walk into the meeting with their product already in their video.' },
  { icon: <IconTarget />, title: 'Hours, not production weeks', body: 'No talent rebooking, no location, no reshoot. Upload, decide, approve, render.' },
  { icon: <IconShield />, title: 'Sign-off clients can trust', body: 'Approval is bound to the exact version. Any edit resets it, and a takedown stops delivery at once.' },
  { icon: <IconUsers />, title: 'Roles for the whole account team', body: 'Editors build placements, managers approve, and clients review as read-only viewers.' },
  { icon: <IconCheck />, title: 'Margin you can see', body: 'From $0.60 per rendered video on the Agency plan. Price it to clients however you like.' },
];

const PRICED_AGAINST = [
  { who: 'AI ad generators', what: 'Creatify Business is $195 a month for 10 seats. Their AI actors replace your footage.' },
  { who: 'Generative editors', what: 'About $2.80 per 10-second edit (reported Runway Aleph API pricing), with no frame-by-frame guarantee.' },
  { who: 'Placement networks', what: 'Rembrand sells CPM campaigns through sales. There is no public price list and no self-serve.' },
];

export default function Landing() {
  return (
    <>
      <SiteHeader />
      <main>
        {/* HERO */}
        <section className="relative -mt-16 overflow-hidden bg-white pt-16 text-ink">
          <div className="relative mx-auto max-w-6xl px-4 pb-14 pt-12 text-center sm:px-6 sm:pb-16 sm:pt-20">
            <Link href="#demo" className="inline-flex max-w-full items-center gap-2 rounded-full border border-line bg-white px-3 py-1 text-[13px] text-ink-2 transition hover:bg-soft">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" /> The decision engine for in-video product placement <IconArrowRight size={13} className="shrink-0" />
            </Link>
            <h1 className="mx-auto mt-7 max-w-4xl font-display text-[40px] leading-[1.04] tracking-tight sm:text-6xl lg:text-7xl">
              Put your products in the videos <span className="mk-gradient-ink inline-block pr-[.12em] italic">you already have.</span>
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-[17px] leading-relaxed text-ink-2 sm:text-lg">
              Drop in two to four product shots and a batch of videos. Visora scans every scene, decides which product fits where, and renders
              placements that pass a frame-by-frame check. No reshoot, and no generative redraw.
            </p>
            <div className="mt-9 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
              <Link href="/login" className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-ink px-6 text-[15px] font-medium text-white transition hover:bg-ink-2">
                Start free <IconArrowRight size={16} />
              </Link>
              <Link href="#demo" className="inline-flex h-12 items-center justify-center gap-2 rounded-xl border border-line-2 bg-white px-6 text-[15px] font-medium text-ink transition hover:bg-soft">
                Watch it decide
              </Link>
            </div>
            <p className="mt-5 text-[13px] text-muted">Free plan · no card · sign in with Google or a magic link</p>

            <div id="demo" className="mx-auto mt-12 max-w-5xl scroll-mt-24 sm:mt-16">
              <SceneLab />
            </div>
          </div>
          <div className="relative overflow-hidden border-y border-line bg-white py-4">
            <div className="mk-marquee flex w-max gap-10 whitespace-nowrap text-sm text-muted">
              {[...MARQUEE, ...MARQUEE].map((m, i) => <span key={i} className="flex items-center gap-10">{m}<span className="text-line-2">✦</span></span>)}
            </div>
          </div>
        </section>

        {/* THREE WAYS */}
        <section className="mx-auto max-w-6xl px-4 py-24 sm:px-6">
          <Reveal className="max-w-2xl">
            <p className="text-sm font-medium text-accent-ink">Why this exists</p>
            <h2 className="mt-2 font-display text-4xl tracking-tight sm:text-5xl">There are three ways to get a product into a video.</h2>
          </Reveal>
          <div className="mt-12 grid gap-5 md:grid-cols-3">
            {OPTIONS.map((o, i) => (
              <Reveal key={o.title} delay={i * 90}>
                <div className={o.tone === 'ours' ? 'mk-dark relative h-full overflow-hidden rounded-3xl p-7 shadow-pop' : 'h-full rounded-3xl border border-line bg-card p-7'}>
                  {o.tone === 'ours' && <div className="mk-glow pointer-events-none absolute inset-0" />}
                  <div className="relative">
                    <p className={o.tone === 'ours' ? 'text-sm text-white/55' : 'text-sm text-muted'}>Option {i + 1}</p>
                    <h3 className="mt-1 text-xl font-semibold">{o.title}</h3>
                    <ul className="mt-6 space-y-3 text-[15px]">
                      {o.points.map(([t, good]) => (
                        <li key={t} className="flex gap-2.5">
                          {good ? <IconCheck size={18} className={o.tone === 'ours' ? 'mt-0.5 shrink-0 text-[#b9b0ff]' : 'mt-0.5 shrink-0 text-good'} /> : <IconX size={18} className="mt-0.5 shrink-0 text-bad" />}
                          <span className={o.tone === 'ours' ? 'text-white/85' : 'text-ink-2'}>{t}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* BEFORE / AFTER */}
        <section className="border-y border-line bg-card/60">
          <div className="mx-auto grid max-w-6xl items-center gap-12 px-4 py-24 sm:px-6 lg:grid-cols-[.85fr_1.15fr]">
            <Reveal>
              <p className="text-sm font-medium text-accent-ink">Same footage, new sponsor</p>
              <h2 className="mt-2 font-display text-4xl tracking-tight sm:text-5xl">The rest of the frame stays exactly as you shot it.</h2>
              <p className="mt-5 leading-relaxed text-ink-2">
                The product is tracked onto the surface and composited in linear light. Nothing else in the frame is touched, and your product
                image is never regenerated, so labels, logos and colours stay true.
              </p>
              <ul className="mt-6 space-y-2.5 text-[15px]">
                {['Follows the surface as the camera moves', 'Holds its true proportions', 'Exports a standard MP4 with the original audio'].map((t) => (
                  <li key={t} className="flex gap-2.5"><IconCheck size={18} className="mt-0.5 shrink-0 text-good" />{t}</li>
                ))}
              </ul>
            </Reveal>
            <Reveal delay={120}><BeforeAfter /></Reveal>
          </div>
        </section>

        {/* HOW IT WORKS */}
        <section id="how" className="mx-auto max-w-6xl px-4 py-24 sm:px-6">
          <Reveal className="text-center">
            <p className="text-sm font-medium text-accent-ink">How it works</p>
            <h2 className="mx-auto mt-2 max-w-2xl font-display text-4xl tracking-tight sm:text-5xl">From product shots to placed video in four steps.</h2>
          </Reveal>
          <div className="relative mt-14 grid gap-8 sm:grid-cols-2 lg:grid-cols-4 lg:gap-5">
            <div className="absolute left-[12%] right-[12%] top-6 hidden h-px bg-gradient-to-r from-transparent via-line-2 to-transparent lg:block" />
            {STEPS.map((s, i) => (
              <Reveal key={s.title} delay={i * 90} className="relative text-center">
                <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl border border-line bg-card text-ink shadow-card">{s.icon}</div>
                <p className="mt-4 font-mono text-xs text-muted">STEP 0{i + 1}</p>
                <h3 className="mt-1 font-semibold">{s.title}</h3>
                <p className="mx-auto mt-1.5 max-w-[240px] text-sm leading-relaxed text-muted">{s.body}</p>
              </Reveal>
            ))}
          </div>
        </section>

        {/* BENTO */}
        <section className="mx-auto max-w-6xl px-4 pb-24 sm:px-6">
          <Reveal className="max-w-2xl">
            <p className="text-sm font-medium text-accent-ink">Built for the work after the demo</p>
            <h2 className="mt-2 font-display text-4xl tracking-tight sm:text-5xl">Decides across a library. Refuses what won’t hold up.</h2>
          </Reveal>
          <div className="mt-12 grid gap-5 lg:grid-cols-3">
            <Reveal className="lg:col-span-2">
              <div className="h-full rounded-3xl border border-line bg-card p-6 sm:p-7">
                <h3 className="font-semibold">One decision, a whole library</h3>
                <p className="mt-1 text-sm text-muted">Products are assigned across videos for the best overall result, and spread out so no single product takes everything.</p>
                <div className="mt-6"><LibraryMatrix /></div>
              </div>
            </Reveal>
            <Reveal delay={90}>
              <div className="h-full rounded-3xl border border-line bg-card p-6 sm:p-7">
                <h3 className="font-semibold">Refuses bad fits, with timestamps</h3>
                <p className="mt-1 text-sm text-muted">Instead of rendering something that breaks, it tells you what went wrong and how to fix it.</p>
                <div className="mt-5"><FailureList /></div>
              </div>
            </Reveal>
            <Reveal>
              <div className="h-full rounded-3xl border border-line bg-card p-6 sm:p-7">
                <h3 className="font-semibold">Every frame measured</h3>
                <p className="mt-1 text-sm text-muted">Tracking, drift, cuts, occlusion, blur and lighting are checked on every frame of the window.</p>
                <div className="mt-5"><FrameTrace /></div>
              </div>
            </Reveal>
            <Reveal delay={90}>
              <div className="h-full rounded-3xl border border-line bg-card p-6 sm:p-7">
                <h3 className="font-semibold">Approval bound to a version</h3>
                <p className="mt-1 text-sm text-muted">Edit anything and the approval resets. Superseded renders stop downloading.</p>
                <div className="mt-5"><VersionTrail /></div>
              </div>
            </Reveal>
            <Reveal delay={180}>
              <div className="h-full rounded-3xl border border-line bg-card p-6 sm:p-7">
                <h3 className="font-semibold">Private by default</h3>
                <p className="mt-1 text-sm text-muted">Media stays in private storage. Downloads use short-lived signed links, and a takedown stops delivery instantly.</p>
                <div className="mt-6 rounded-2xl border border-line bg-canvas p-4 font-mono text-[11.5px] text-ink-2">
                  <p className="truncate">GET /media/<span className="text-accent-ink">9f2c…e71a</span></p>
                  <p className="mt-1 flex items-center justify-between"><span>expires in</span><span className="rounded bg-warn-soft px-1.5 text-warn">04:59</span></p>
                  <p className="mt-1 flex items-center justify-between"><span>workspace</span><span>yours only</span></p>
                </div>
              </div>
            </Reveal>
          </div>
        </section>

        {/* AGENCIES */}
        <section id="agencies" className="mk-dark relative scroll-mt-16 overflow-hidden">
          <div className="mk-grid pointer-events-none absolute inset-0 opacity-60" />
          <div className="relative mx-auto max-w-6xl px-4 py-24 sm:px-6">
            <Reveal className="max-w-3xl">
              <p className="text-sm font-medium text-[#b9b0ff]">For agencies</p>
              <h2 className="mt-2 font-display text-4xl tracking-tight sm:text-6xl">Why agencies spend their time here.</h2>
              <p className="mt-5 max-w-2xl text-lg leading-relaxed text-white/60">
                Your clients already sit on hours of usable footage. Visora turns it into placements you can pitch, approve and deliver
                in an afternoon, on a plan that costs less than one reshoot day.
              </p>
            </Reveal>
            <div className="mt-14 grid gap-px overflow-hidden rounded-3xl border border-white/10 bg-white/10 sm:grid-cols-2 lg:grid-cols-3">
              {AGENCY.map((a, i) => (
                <Reveal key={a.title} delay={(i % 3) * 80} className="h-full">
                  <div className="h-full bg-[#0f0e14] p-7 transition hover:bg-[#15141c]">
                    <div className="grid h-10 w-10 place-items-center rounded-xl bg-white/[.07] text-[#c9c2ff]">{a.icon}</div>
                    <h3 className="mt-5 font-semibold">{a.title}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-white/55">{a.body}</p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
          <div className="relative bg-canvas text-ink">
            <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
              <Reveal><AgencyCalculator /></Reveal>
            </div>
          </div>
        </section>

        {/* PRICING */}
        <section id="pricing" className="scroll-mt-16 border-t border-line">
          <div className="mx-auto max-w-6xl px-4 py-24 text-center sm:px-6">
            <Reveal>
              <p className="text-sm font-medium text-accent-ink">Pricing</p>
              <h2 className="mx-auto mt-2 max-w-3xl font-display text-4xl tracking-tight sm:text-5xl">Priced like a tool, not a production budget.</h2>
              <p className="mx-auto mt-4 max-w-2xl text-ink-2">Every plan includes the decision engine, the frame-by-frame check and version-bound approvals. Pay for the volume you need.</p>
            </Reveal>
            <PlanCards />
            <Reveal className="mt-16 text-left">
              <div className="rounded-3xl border border-line bg-card p-6 sm:p-8">
                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
                  <div>
                    <p className="text-sm font-medium text-accent-ink">How we priced it</p>
                    <h3 className="mt-1 text-xl font-semibold">What the alternatives cost</h3>
                  </div>
                  <Link href="/pricing" className="inline-flex items-center gap-1.5 text-sm font-medium hover:underline">Full comparison <IconArrowRight size={14} /></Link>
                </div>
                <div className="mt-6 grid gap-4 md:grid-cols-3">
                  {PRICED_AGAINST.map((p) => (
                    <div key={p.who} className="rounded-2xl bg-canvas p-5">
                      <p className="font-medium">{p.who}</p>
                      <p className="mt-1.5 text-sm leading-relaxed text-muted">{p.what}</p>
                    </div>
                  ))}
                </div>
                <p className="mt-4 text-xs text-muted">From the vendors’ pricing pages and public reports, checked 31 October 2026. Prices change often.</p>
              </div>
            </Reveal>
          </div>
        </section>

        {/* FAQ */}
        <section id="faq" className="scroll-mt-16 border-t border-line bg-card/60">
          <div className="mx-auto grid max-w-6xl gap-10 px-4 py-24 sm:px-6 lg:grid-cols-[.8fr_1.2fr]">
            <div>
              <h2 className="font-display text-4xl tracking-tight sm:text-5xl">Questions</h2>
              <p className="mt-4 text-ink-2">Something else? Start a free workspace and try it on your own footage.</p>
            </div>
            <div className="divide-y divide-line border-y border-line">
              {FAQ.map((f) => (
                <details key={f.q} className="group py-5">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-medium">
                    {f.q}<span className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-line text-muted transition group-open:rotate-45">+</span>
                  </summary>
                  <p className="mt-3 text-[15px] leading-relaxed text-ink-2">{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="mk-dark relative overflow-hidden">
          <div className="mk-glow pointer-events-none absolute inset-0" />
          <div className="mk-grain pointer-events-none absolute inset-0" />
          <div className="relative mx-auto max-w-6xl px-4 py-28 text-center sm:px-6">
            <h2 className="mx-auto max-w-3xl font-display text-5xl leading-[1.05] tracking-tight sm:text-7xl">
              Your next sponsored integration is <span className="mk-gradient-text inline-block pr-[.12em] italic">already filmed.</span>
            </h2>
            <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link href="/login" className="inline-flex h-12 items-center gap-2 rounded-xl bg-white px-6 text-[15px] font-medium text-[#0c0b10] transition hover:bg-white/90">Start free <IconArrowRight size={16} /></Link>
              <Link href="/pricing" className="inline-flex h-12 items-center rounded-xl border border-white/15 px-6 text-[15px] font-medium text-white transition hover:bg-white/10">Compare plans</Link>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
