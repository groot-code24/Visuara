import Link from 'next/link';
import type { ReactNode } from 'react';
import { SiteFooter, SiteHeader } from './site';

export type LegalSection = { heading: string; body: ReactNode };

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

export function LegalPage({ title, updated, intro, summary, sections, other }: {
  title: string; updated: string; intro: ReactNode; summary: string[]; sections: LegalSection[]; other: { href: string; label: string };
}) {
  return (
    <>
      <SiteHeader />
      <main className="mx-auto max-w-6xl px-4 pb-24 pt-14 sm:px-6 sm:pt-20">
        <header className="max-w-3xl">
          <p className="mk-eyebrow">Legal</p>
          <h1 className="mt-4 font-display text-5xl leading-[1.02] sm:text-6xl">{title}</h1>
          <p className="mt-4 font-mono text-xs text-muted">Effective {updated}</p>
          <div className="mt-6 text-lg leading-relaxed text-ink-2">{intro}</div>
        </header>

        <div className="mt-14 grid gap-12 lg:grid-cols-[240px_minmax(0,1fr)] lg:gap-16">
          <nav aria-label="On this page" className="lg:sticky lg:top-24 lg:self-start">
            <p className="font-mono text-xs uppercase tracking-[.08em] text-muted">On this page</p>
            <ol className="mt-3 space-y-1 text-sm">
              {sections.map((s, i) => (
                <li key={s.heading}>
                  <a href={`#${slug(s.heading)}`} className="flex gap-2.5 rounded-md py-1 text-ink-2 transition hover:text-ink">
                    <span className="font-mono text-xs leading-5 text-muted">{String(i + 1).padStart(2, '0')}</span>{s.heading}
                  </a>
                </li>
              ))}
            </ol>
            <Link href={other.href} className="mt-5 inline-block border-t border-line pt-4 text-sm font-medium hover:underline">{other.label}</Link>
          </nav>

          <div className="min-w-0 max-w-3xl">
            <section aria-label="Summary" className="rounded-2xl border border-line bg-card p-6">
              <h2 className="font-semibold text-ink">The short version</h2>
              <ul className="mt-3 space-y-2 text-[15px] leading-relaxed text-ink-2">
                {summary.map((s) => <li key={s} className="flex gap-2.5"><span className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full bg-ink" />{s}</li>)}
              </ul>
              <p className="mt-4 text-xs leading-relaxed text-muted">This summary is for convenience. The full text below is what applies.</p>
            </section>
            {sections.map((s, i) => (
              <section key={s.heading} id={slug(s.heading)} className="mt-12 scroll-mt-24">
                <h2 className="flex gap-3 font-display text-2xl text-ink">
                  <span className="pt-1.5 font-mono text-xs font-normal tracking-normal text-muted">{String(i + 1).padStart(2, '0')}</span>{s.heading}
                </h2>
                <div className="mt-4 space-y-3 text-[15px] leading-relaxed text-ink-2 [&_a]:underline [&_li]:mt-1.5 [&_strong]:font-medium [&_strong]:text-ink [&_ul]:list-disc [&_ul]:pl-5">{s.body}</div>
              </section>
            ))}
          </div>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}

/** A small reference table that scrolls on its own on a phone, so the page never does. */
export function LegalTable({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[520px] border-collapse text-left text-sm">
        <thead className="bg-soft text-xs text-muted">
          <tr>{head.map((h) => <th key={h} scope="col" className="px-4 py-2.5 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line align-top">
          {rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="px-4 py-3">{c}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  );
}

export function ContactLine({ email }: { email: string }) {
  return email ? <a href={`mailto:${email}`}>{email}</a> : <>the contact address shown in your account emails</>;
}
