import { Scene } from '@/components/marketing/scene';
import { IconCheck } from '@/components/icons';

const POINTS = [
  'Finds every usable surface in every shot',
  'Decides which product fits where, and shows its reasoning',
  'Checks every frame before anything renders',
];

/** The dark side of the sign-in page: what Visuara does, in one picture and three lines. Illustrative only. */
export function SignInAside() {
  return (
    <div className="relative flex h-full flex-col overflow-hidden mk-dark px-10 py-12 xl:px-14">
      <div className="mk-glow pointer-events-none absolute inset-0 opacity-90" />
      <div className="mk-grid pointer-events-none absolute inset-0" />
      <div className="relative flex flex-1 flex-col justify-center">
        <p className="mk-eyebrow mk-eyebrow-dark">Visuara</p>
        <h2 className="mt-4 max-w-[460px] font-display text-[38px] leading-[1.06] text-white xl:text-[44px]">
          Your product, in <span className="mk-gradient-text">every scene you already own.</span>
        </h2>
        <div className="mk-ring mk-float relative mt-8 overflow-hidden rounded-2xl bg-black">
          <div className="aspect-video"><Scene placed={{ product: 'north', surface: 'poster' }} overlays={[{ surface: 'poster', tone: 'winner' }, { surface: 'screen', tone: 'candidate' }]} /></div>
          <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 font-mono text-[10.5px] uppercase tracking-wider text-white/85 backdrop-blur">
            <span className="h-1.5 w-1.5 rounded-full bg-trust" /> Placed · every frame checked
          </div>
        </div>
        <ul className="mt-8 space-y-3 text-[15px] text-white/80">
          {POINTS.map((t) => <li key={t} className="flex gap-3"><span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-trust/15 text-trust"><IconCheck size={12} /></span>{t}</li>)}
        </ul>
        <p className="mt-10 border-t border-white/10 pt-5 text-xs text-white/45">Deterministic computer vision: no generative model ever redraws your product or your footage.</p>
      </div>
    </div>
  );
}
