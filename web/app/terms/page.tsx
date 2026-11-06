import type { Metadata } from 'next';
import Link from 'next/link';
import { ContactLine, LegalPage } from '@/components/legal';
import { CONTACT_EMAIL, LEGAL_JURISDICTION, LEGAL_NAME } from '@/lib/site';

export const metadata: Metadata = { title: 'Terms of Service', description: 'The agreement between you and Visuara when you use the service.' };

export default function Terms() {
  return (
    <LegalPage
      title="Terms of Service"
      updated="3 November 2026"
      other={{ href: '/privacy', label: 'Read the Privacy Policy' }}
      intro={<p>These terms are an agreement between you and {LEGAL_NAME} (&ldquo;Visuara&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;). They apply when you create an account or use the Visuara website and service.</p>}
      summary={[
        'You keep ownership of your footage, product images and renders.',
        'You must have the rights to everything you upload, including the brands and people shown in it.',
        'Automated checks support your judgement. You decide what to approve and publish.',
        'Visuara is in pilot, so features and limits may change and the service comes without guarantees.',
      ]}
      sections={[
        {
          heading: 'Accepting these terms',
          body: (
            <>
              <p>By creating an account or using the service, you agree to these terms and confirm that you are at least 18 years old. If you do not agree, do not use the service.</p>
              <p>If you use Visuara for a company or client, you confirm that you are authorised to accept these terms on its behalf, and &ldquo;you&rdquo; includes that organisation.</p>
            </>
          ),
        },
        {
          heading: 'The service',
          body: (
            <>
              <p>Visuara analyses the videos and product images you upload, recommends which product fits which surface, and renders the placements you approve.</p>
              <p>The service is in pilot. We may add, change or remove features and limits as it develops. We will try to give you reasonable notice of a change that materially reduces what you can do.</p>
            </>
          ),
        },
        {
          heading: 'Accounts and workspaces',
          body: (
            <ul>
              <li>You sign in with a magic link or one-time code sent to your email, or with Google. Keep access to those accounts secure, because they are the keys to your Visuara account.</li>
              <li>Each account belongs to a workspace. The workspace owner controls who is a member and what role they hold, and is responsible for what members do there.</li>
              <li>Give accurate details, and tell us promptly if you believe someone has accessed your account without permission.</li>
            </ul>
          ),
        },
        {
          heading: 'Your content',
          body: (
            <>
              <p>&ldquo;Your content&rdquo; means the videos, product images and 3D models you upload, and the previews and renders created from them for you. You keep all rights you hold in it.</p>
              <p>You give us a worldwide, non-exclusive, royalty-free licence to host, copy, process and display your content only as needed to provide the service to you, and for no other purpose. The licence ends when your content is deleted, apart from the short time described in the <Link href="/privacy">Privacy Policy</Link>. We do not use your content to train AI models.</p>
            </>
          ),
        },
        {
          heading: 'Rights and advertising rules',
          body: (
            <>
              <p>You are responsible for your content and for how you use the renders. You confirm that you hold all the rights and permissions you need, including for:</p>
              <ul>
                <li>the footage itself, including any music, and any contract with its creator or publisher;</li>
                <li>the products, trademarks and brands you place or that already appear in the footage;</li>
                <li>any identifiable people in the footage, and their consent where the law or a contract requires it, including to being shown alongside a product.</li>
              </ul>
              <p>Adding a product to a video can make it advertising. You are responsible for following advertising law and platform rules, including any requirement to disclose sponsorship or altered content.</p>
            </>
          ),
        },
        {
          heading: 'Acceptable use',
          body: (
            <>
              <p>You must not use Visuara to:</p>
              <ul>
                <li>upload content that is illegal, that you have no right to use, or that sexualises or exploits children;</li>
                <li>deceive people, impersonate anyone, or falsely suggest that a person or organisation endorses a product;</li>
                <li>place products in content in a way that is defamatory, harassing or discriminatory;</li>
                <li>upload malware, or probe, overload or get around the security or limits of the service;</li>
                <li>access another workspace, or scrape, resell or copy the service to build a competing product.</li>
              </ul>
            </>
          ),
        },
        {
          heading: 'Automated results',
          body: <p>Recommendations, scores and quality checks are automated measurements and can be wrong. They do not guarantee that a placement is suitable, lawful or accurate. Review every render before you use it. You make the final decision on what to approve and publish.</p>,
        },
        {
          heading: 'Plans and payment',
          body: (
            <>
              <p>Plans and their limits are shown on the <Link href="/pricing">pricing page</Link>, and limits are enforced automatically. Hosting capacity may also limit video length, file size or total storage, and the service tells you when a limit applies.</p>
              <p>Paid plans are billed in advance, monthly or yearly, through Lemon Squeezy, which sells the subscription to you as our merchant of record and adds any sales tax or VAT at checkout. A subscription renews until you cancel it; a cancelled plan stays active until the end of the period you paid for. Enterprise plans are arranged with us directly and billed by invoice; their fees exclude taxes unless stated. Unless the law requires otherwise, fees already paid are not refundable. We will give at least 30 days&rsquo; notice of a price increase on your plan. If you stop paying, we may move your workspace to the Free plan.</p>
            </>
          ),
        },
        {
          heading: 'Copyright complaints',
          body: <p>If you believe content on Visuara infringes your rights, write to <ContactLine email={CONTACT_EMAIL} /> with your contact details, the work concerned, where the content appears and a statement that your complaint is accurate and made in good faith. We may remove or block content, and close the accounts of people who repeatedly infringe.</p>,
        },
        {
          heading: 'Our service and feedback',
          body: <p>We and our licensors own the Visuara software, brand and website. These terms give you a limited right to use the service and transfer no other rights. If you send us feedback, we may use it without any obligation to you.</p>,
        },
        {
          heading: 'Suspension and closing an account',
          body: (
            <>
              <p>You can stop using Visuara at any time and ask us to delete your account and data. We may suspend or close an account, or remove content, if we reasonably believe it breaks these terms, creates legal risk, or puts the service or other customers at risk. Where we can, and where it is lawful, we will tell you first.</p>
              <p>The sections that by their nature should continue, including those on liability and governing law, continue after your account closes.</p>
            </>
          ),
        },
        {
          heading: 'Disclaimers',
          body: <p>To the fullest extent the law allows, the service is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;, without warranties of any kind, including fitness for a particular purpose and non-infringement. We do not promise that it will be uninterrupted or error-free, or that stored content can never be lost, so keep your own copies of your source files and renders.</p>,
        },
        {
          heading: 'Limit of liability',
          body: (
            <>
              <p>To the fullest extent the law allows, we are not liable for indirect, incidental, special or consequential losses, or for lost profits, revenue, goodwill or data. Our total liability for all claims relating to the service is limited to the greater of the amount you paid us in the 12 months before the claim arose and US$100.</p>
              <p>Nothing in these terms limits liability that cannot legally be limited, or removes rights you have as a consumer under the law where you live.</p>
            </>
          ),
        },
        {
          heading: 'Your responsibility for claims',
          body: <p>If you use Visuara for business, you will cover our reasonable losses and costs from third-party claims arising from your content, your use of renders, or your breach of these terms. We will tell you promptly about any such claim and will not settle it in a way that binds you without your agreement.</p>,
        },
        {
          heading: 'Governing law',
          body: <p>These terms are governed by the laws of {LEGAL_JURISDICTION || <>the place where {LEGAL_NAME} is established</>}, and its courts have jurisdiction over any dispute, except where the law where you live as a consumer gives you the right to bring a claim there.</p>,
        },
        {
          heading: 'Changes and general terms',
          body: (
            <>
              <p>We may update these terms. For a material change, we will update the effective date and email workspace owners before it takes effect. If you continue to use the service afterwards, the new terms apply; if you do not agree, you can stop using it and close your account.</p>
              <p>These terms are the whole agreement between us about the service. If part of them cannot be enforced, the rest still applies. Not enforcing a right does not waive it. You may not transfer these terms without our consent, and we may transfer them as part of a reorganisation or sale of the business. Neither of us is liable for delays caused by events outside our reasonable control.</p>
            </>
          ),
        },
        {
          heading: 'Contact',
          body: <p>Questions about these terms: <ContactLine email={CONTACT_EMAIL} />.</p>,
        },
      ]}
    />
  );
}
