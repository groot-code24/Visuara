// Mirrors app/limits.py PLANS (the API enforces these allowances). Annual billing is 25% off.
export type Plan = {
  id: 'free' | 'pro' | 'team' | 'enterprise'; name: string; monthly: number | null; annual: number | null; tagline: string;
  cta: string; badge?: string; renders: number | null; features: string[];
};

export const PLANS: Plan[] = [
  {
    id: 'free', name: 'Free', monthly: 0, annual: 0, renders: 3, tagline: 'Run the decision engine on your own footage.', cta: 'Start free',
    features: ['5 decisions a month', '3 rendered videos a month', 'Up to 3 videos per decision', 'Videos up to 45 seconds and 200 MB', '2 GB storage', '1 seat'],
  },
  {
    id: 'pro', name: 'Pro', monthly: 39, annual: 29, renders: 40, tagline: 'For creators and in-house marketers placing products every week.', cta: 'Start with Pro',
    features: ['60 decisions a month', '40 rendered videos a month', 'Up to 10 videos per decision', 'Videos up to 3 minutes and 500 MB', '50 GB storage', '3 seats'],
  },
  {
    id: 'team', name: 'Agency', monthly: 159, annual: 119, renders: 200, badge: 'Best for agencies', tagline: 'For agencies running placements across several clients.', cta: 'Start with Agency',
    features: ['300 decisions a month', '200 rendered videos a month', 'Up to 20 videos per decision', 'Videos up to 4 minutes and 1 GB', '250 GB storage', '15 seats; invite clients as viewers', 'Manager approval before any render'],
  },
  {
    id: 'enterprise', name: 'Enterprise', monthly: null, annual: null, renders: null, tagline: 'Volume, dedicated render capacity and your own cloud.', cta: 'Talk to us',
    features: ['Custom decision and render volume', 'Up to 50 videos per decision', 'Dedicated render workers', 'Deployment in your cloud account', 'Invoiced billing', 'Priority support'],
  },
];

export const ANNUAL_SAVING = '25%';

/** Price per rendered video if the whole monthly allowance is used. */
export function perRender(p: Plan, annual: boolean): string | null {
  const price = annual ? p.annual : p.monthly;
  if (price == null || !p.renders) return null;
  return price === 0 ? '$0' : `$${(Math.round((price * 100) / p.renders) / 100).toFixed(2)}`;
}

export const COMPARISON_ROWS: { label: string; values: [string, string, string, string] }[] = [
  { label: 'Decisions per month', values: ['5', '60', '300', 'Custom'] },
  { label: 'Rendered videos per month', values: ['3', '40', '200', 'Custom'] },
  { label: 'Products per decision', values: ['Up to 4', 'Up to 4', 'Up to 4', 'Up to 4'] },
  { label: 'Videos per decision', values: ['3', '10', '20', '50'] },
  { label: 'Maximum video length', values: ['45 s', '3 min', '4 min', 'Platform limit'] },
  { label: 'Maximum file size', values: ['200 MB', '500 MB', '1 GB', 'Custom'] },
  { label: 'Video uploads per day', values: ['10', '50', '200', 'Custom'] },
  { label: 'Renders running at once', values: ['2', '4', '10', 'Custom'] },
  { label: 'Storage', values: ['2 GB', '50 GB', '250 GB', 'Custom'] },
  { label: 'Seats', values: ['1', '3', '15', 'Custom'] },
  { label: 'Frame-by-frame quality check', values: ['✓', '✓', '✓', '✓'] },
  { label: 'Version-bound approvals and takedown', values: ['✓', '✓', '✓', '✓'] },
  { label: 'Roles: editor, manager, client viewer', values: ['—', '✓', '✓', '✓'] },
  { label: 'Price per rendered video (annual)', values: ['$0', '$0.73', '$0.60', 'Volume'] },
];

// Competitive landscape. Prices read from the vendors' own pricing pages on 31 October 2026 where they publish one,
// otherwise third-party reports; they change often.
export const LANDSCAPE = [
  {
    kind: 'AI ad and UGC generators', examples: 'Creatify, Topview, Arcads',
    price: 'Creatify Pro $55/month ($38 annual), Business $195/month for 10 seats. Topview from $36/month annual, Team $56 per seat. Arcads publishes no price list.',
    difference: 'They generate new ads with AI actors. We put your real product into footage you already own.',
  },
  {
    kind: 'Generative video editors', examples: 'Runway Aleph, Kling, Pika',
    price: 'Runway Aleph API reported at about $0.28 per edited second (≈ $2.80 for 10 s). Pika from $8/month.',
    difference: 'Prompt-driven edits with no guarantee the product stays exact or stable. We check every frame and never redraw the product.',
  },
  {
    kind: 'Virtual product placement networks', examples: 'Rembrand (now including Mirriad’s US business)',
    price: 'Sales-led campaigns priced per thousand impressions. No self-serve price list; “Contact us” only.',
    difference: 'Weeks of sales cycle and their inventory. We are self-serve: your products, your videos, today.',
  },
];

export const FAQ = [
  { q: 'What do I need to start?', a: 'Two to four product images (a PNG with a transparent background looks best) and the videos you want them in. You must own the videos or be licensed to edit them.' },
  { q: 'How does the decision engine choose?', a: 'It finds flat surfaces in each video (walls, signs, screens, tabletops), tracks them, and scores every product on each one: whether it passes our quality check, screen time, on-screen size, shape fit, tracking stability, contrast and how well the surface suits the product category. Then it assigns products across your videos to get the best overall result, and explains every choice.' },
  { q: 'Is anything rendered without my approval?', a: 'No. A recommendation becomes a draft placement, which must pass a frame-by-frame quality check and be approved by a manager in your workspace. Any change creates a new version that needs approval again.' },
  { q: 'Can my clients review placements?', a: 'Yes. Invite them to your workspace as viewers: they can see decisions, previews, placements and renders, but cannot change or approve anything. Viewers use a seat.' },
  { q: 'What kinds of placements work today?', a: 'Products shown on flat, rigid surfaces that stay visible: posters, signs, screens, packaging faces and tabletops. Scenes with something passing in front of the surface, scene cuts, heavy motion blur or strong lighting changes are refused rather than rendered badly.' },
  { q: 'Do you use generative AI on my footage?', a: 'No. Placement is deterministic computer vision and compositing, so the product is never redrawn or altered, and the rest of the frame is untouched.' },
  { q: 'How do upgrades and billing work?', a: 'During the pilot, plan changes are set up by our team and billed by invoice. Your workspace shows its plan and this month’s usage at all times.' },
];
