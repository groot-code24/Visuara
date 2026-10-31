import type { Metadata } from 'next';
import { ContactLine, LegalPage } from '@/components/legal';
import { CONTACT_EMAIL } from '@/lib/site';

export const metadata: Metadata = { title: 'Terms of Service', description: 'The terms for using Context Commerce.' };

export default function Terms() {
  return (
    <LegalPage
      title="Terms of Service"
      updated="31 October 2026"
      intro={<p>These terms apply when you use Context Commerce. By creating an account or using the service, you agree to them. If you use it for a company, you confirm that you may accept these terms on its behalf.</p>}
      sections={[
        {
          heading: 'The service',
          body: <p>Context Commerce analyses the videos and product images you upload, recommends where products fit, and renders the placements you approve. It is in pilot, so features and limits may change as we improve it.</p>,
        },
        {
          heading: 'Your account',
          body: <p>You are responsible for your workspace, for the people you invite to it, and for what they do there. Keep access to your email account secure, because it is how you sign in.</p>,
        },
        {
          heading: 'Your content and rights',
          body: (
            <>
              <p>You keep ownership of your videos, product images and renders. You give us permission to store and process them only as needed to run the service for you.</p>
              <p>You confirm that you own, or are licensed to use and edit, everything you upload, including the footage, the products and brands shown, and any people who appear in it. If a placement could be seen as advertising, you are responsible for any disclosure the law or a platform requires.</p>
            </>
          ),
        },
        {
          heading: 'Acceptable use',
          body: (
            <ul>
              <li>Do not upload content you have no right to use, or content that is illegal.</li>
              <li>Do not use the service to deceive people or to place products in a way that misrepresents someone.</li>
              <li>Do not try to break, overload or get around the security or limits of the service, or access another workspace.</li>
            </ul>
          ),
        },
        {
          heading: 'Plans and payment',
          body: <p>Plans and their limits are described on the pricing page. During the pilot, paid plans are set up by our team and billed by invoice. If you stop paying for a plan, we may move your workspace to the Free plan.</p>,
        },
        {
          heading: 'Automated checks',
          body: <p>Recommendations and quality checks are automated measurements, not a guarantee that a placement suits your purpose. You decide what to approve and publish, and you should review every render before you use it.</p>,
        },
        {
          heading: 'Suspension and ending',
          body: <p>You can stop using the service at any time and ask us to delete your data. We may suspend or close accounts that break these terms or put the service or other customers at risk.</p>,
        },
        {
          heading: 'Disclaimers and liability',
          body: <p>The service is provided as is during the pilot, without warranties, to the extent the law allows. To the extent the law allows, we are not liable for indirect or consequential losses, and our total liability is limited to the amount you paid us in the 12 months before the claim.</p>,
        },
        {
          heading: 'Changes',
          body: <p>We may update these terms. If a change is significant, we will update the date above and tell workspace owners by email before it takes effect.</p>,
        },
        {
          heading: 'Contact',
          body: <p>Questions about these terms: <ContactLine email={CONTACT_EMAIL} />.</p>,
        },
      ]}
    />
  );
}
