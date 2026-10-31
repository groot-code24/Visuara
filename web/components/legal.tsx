import type { ReactNode } from 'react';
import { SiteFooter, SiteHeader } from './site';

export function LegalPage({ title, updated, intro, sections }: { title: string; updated: string; intro: ReactNode; sections: { heading: string; body: ReactNode }[] }) {
  return (
    <>
      <SiteHeader />
      <main className="mx-auto max-w-3xl px-4 pb-24 pt-14 sm:px-6">
        <h1 className="font-display text-5xl tracking-tight">{title}</h1>
        <p className="mt-3 text-sm text-muted">Last updated {updated}</p>
        <div className="mt-8 text-[15px] leading-relaxed text-ink-2">{intro}</div>
        {sections.map((s) => (
          <section key={s.heading} className="mt-10">
            <h2 className="text-xl font-semibold text-ink">{s.heading}</h2>
            <div className="mt-3 space-y-3 text-[15px] leading-relaxed text-ink-2 [&_li]:mt-1.5 [&_ul]:list-disc [&_ul]:pl-5">{s.body}</div>
          </section>
        ))}
      </main>
      <SiteFooter />
    </>
  );
}

export function ContactLine({ email }: { email: string }) {
  return email ? <a className="underline" href={`mailto:${email}`}>{email}</a> : <>your Context Commerce contact</>;
}
