import type { Metadata } from 'next';
import Link from 'next/link';
import { ContactLine, LegalPage, LegalTable } from '@/components/legal';
import { CONTACT_EMAIL, LEGAL_NAME } from '@/lib/site';

export const metadata: Metadata = { title: 'Privacy Policy', description: 'What Visuara collects, why, who handles it, how long it is kept, and your rights.' };

export default function Privacy() {
  return (
    <LegalPage
      title="Privacy Policy"
      updated="3 November 2026"
      other={{ href: '/terms', label: 'Read the Terms of Service' }}
      intro={<p>This policy explains what personal data {LEGAL_NAME} (&ldquo;Visuara&rdquo;, &ldquo;we&rdquo;) collects when you use our website and service, why we collect it, who handles it, and the choices you have.</p>}
      summary={[
        'We collect what we need to sign you in and run the service, and nothing for advertising.',
        'We do not sell your data, show ads, or use your footage or product images to train AI models.',
        'Your media is kept in private storage and is reached only through short-lived links after an access check.',
        'You can ask us for a copy of your data, or ask us to correct or delete it, at any time.',
      ]}
      sections={[
        {
          heading: 'Who is responsible',
          body: (
            <>
              <p>{LEGAL_NAME} is the controller of account and usage data. Contact us at <ContactLine email={CONTACT_EMAIL} />.</p>
              <p>For the videos and images a workspace uploads, the customer decides what is uploaded and why, and we process that content on the customer&rsquo;s instructions. If you appear in a video processed through Visuara and have a question, please contact the organisation that uploaded it. You can also write to us and we will help you reach them.</p>
            </>
          ),
        },
        {
          heading: 'What we collect',
          body: (
            <LegalTable
              head={['Category', 'What it includes', 'Where it comes from']}
              rows={[
                ['Account', 'Name, email address, and a Google account identifier if you use Google sign-in. We do not use passwords.', 'You, or Google at your request'],
                ['Workspace', 'Workspace name, members, roles, plan and invitations, including the email address of each person invited.', 'You and your colleagues'],
                ['Your content', 'Videos, product images and 3D models, plus what the service creates from them: keyframes, tracking data, previews, decisions and renders.', 'You'],
                ['Activity', 'An audit log of actions such as approvals and downloads, and how much of your plan you have used.', 'Your use of the service'],
                ['Technical', 'IP address, used for sign-in protection and rate limits, and request logs with the path, status and timing. Request logs leave out email addresses, tokens and query strings.', 'Your browser or device'],
              ]}
            />
          ),
        },
        {
          heading: 'Google sign-in',
          body: (
            <>
              <p>If you choose Google sign-in, Google shares your name, email address and account identifier with us, using the <code>openid</code>, <code>email</code> and <code>profile</code> scopes. We use them only to create and secure your account. We cannot see your Google password, and we do not access your Gmail, Drive, Contacts, Calendar or YouTube data.</p>
              <p>Visuara&rsquo;s use and transfer of information received from Google APIs adheres to the <a href="https://developers.google.com/terms/api-services-user-data-policy" rel="noopener noreferrer" target="_blank">Google API Services User Data Policy</a>, including the Limited Use requirements. We do not sell this information, use it for advertising, or use it to train AI models.</p>
            </>
          ),
        },
        {
          heading: 'How we use data and our legal bases',
          body: (
            <>
              <LegalTable
                head={['Purpose', 'Legal basis, where one is required']}
                rows={[
                  ['Sign you in and provide the service you asked for, including analysing and rendering your videos', 'Performance of our contract with you'],
                  ['Send sign-in, invitation and essential account emails', 'Performance of our contract with you'],
                  ['Protect the service: rate limits, abuse prevention and audit records', 'Our legitimate interest in keeping the service and its customers secure'],
                  ['Operate and improve reliability using technical logs and usage counts', 'Our legitimate interest in running a dependable service'],
                  ['Comply with the law and respond to lawful requests', 'Legal obligation'],
                ]}
              />
              <p><strong>What we do not do.</strong> We do not sell or &ldquo;share&rdquo; personal data as California law defines those terms, and we do not show advertising. We do not use your footage or product images to train AI models. Visuara tracks surfaces in a video, and it does not use facial recognition or try to identify the people who appear in it. Automated results are recommendations and do not produce legal or similarly significant decisions about people.</p>
            </>
          ),
        },
        {
          heading: 'Cookies',
          body: (
            <>
              <p>We use only strictly necessary cookies, so there is no cookie banner. There are no analytics or advertising cookies.</p>
              <LegalTable
                head={['Cookie', 'Purpose', 'Lasts']}
                rows={[
                  [<code key="a">cc_session</code>, 'Keeps you signed in', '7 days, or until you sign out'],
                  [<code key="b">cc_csrf</code>, 'Protects against forged requests', 'Browser session'],
                  [<code key="c">cc_oauth</code>, 'Protects the Google sign-in step', 'Minutes'],
                  [<code key="d">cc_signup</code>, 'Remembers your place while you create an account', 'Minutes'],
                ]}
              />
            </>
          ),
        },
        {
          heading: 'Who handles your data',
          body: (
            <>
              <p>We use these service providers, who process data for us under their own data-protection terms and security programmes:</p>
              <LegalTable
                head={['Provider', 'What it does']}
                rows={[
                  ['Vercel', 'Hosts the website and API, runs video processing, and stores media privately'],
                  ['Neon', 'Hosts the database'],
                  ['Our email provider', 'Delivers sign-in and invitation emails'],
                  ['Google', 'Provides sign-in, if you choose it'],
                ]}
              />
              <p>Members of your workspace can see its content, according to their role. Apart from that, we disclose data only when the law requires it, to protect people or the service from harm, or to a successor in a merger or sale, in which case this policy continues to apply until you are told otherwise.</p>
            </>
          ),
        },
        {
          heading: 'International transfers',
          body: <p>Our providers process data in the United States and may process it in other countries, so your data may be handled outside the country where you live. Where the law requires safeguards for those transfers, we rely on measures such as the standard contractual clauses in our providers&rsquo; data-protection terms.</p>,
        },
        {
          heading: 'How long we keep data',
          body: (
            <>
              <LegalTable
                head={['Data', 'How long']}
                rows={[
                  ['Your content and audit records', 'For the life of the workspace, or until you ask us to delete them'],
                  ['Sign-in links and codes', 'Valid for 10 minutes and usable once, then deleted automatically, normally within about a day'],
                  ['Sessions', 'Valid for up to 7 days, then deleted automatically'],
                  ['Abandoned uploads', 'Deleted automatically after the upload window closes, normally within about a day'],
                  ['IP addresses used for rate limits', 'Normally up to about a day'],
                  ['Request logs', 'A short period set by our hosting provider'],
                  ['A removed member', 'Their email address is replaced at once and their sessions are deleted. A record of the change stays in the audit log.'],
                ]}
              />
              <p>When you ask us to delete a workspace, we remove it from our active systems within 30 days. Copies in our providers&rsquo; backups expire on those providers&rsquo; schedules.</p>
            </>
          ),
        },
        {
          heading: 'Security',
          body: (
            <>
              <p>We use technical and organisational measures designed to protect your data, including:</p>
              <ul>
                <li>encryption in transit (HTTPS) and HTTP-only, secure session cookies;</li>
                <li>private media storage, reached only after an access check and through links that expire in minutes;</li>
                <li>sign-in codes and session tokens stored only in hashed form;</li>
                <li>isolation between workspaces, and role checks on every request.</li>
              </ul>
              <p>No system is perfectly secure. If a breach affects your personal data, we will notify you and the relevant authorities as the law requires. Please report suspected security problems to <ContactLine email={CONTACT_EMAIL} />.</p>
            </>
          ),
        },
        {
          heading: 'Your rights',
          body: (
            <>
              <p>Depending on where you live, including the EEA, the UK, California and India, you may have the right to:</p>
              <ul>
                <li>access your personal data and receive a copy in a portable format;</li>
                <li>correct inaccurate data, or have your data deleted;</li>
                <li>object to or restrict certain processing, and withdraw consent where we rely on it;</li>
                <li>appoint someone to act for you where the law allows, and not be treated worse for using your rights;</li>
                <li>complain to your data protection authority.</li>
              </ul>
              <p>To use these rights, write to <ContactLine email={CONTACT_EMAIL} /> from your account email address. We may need to confirm it is you, and we aim to respond within 30 days. If your data is part of a customer&rsquo;s workspace, we may refer your request to that customer.</p>
            </>
          ),
        },
        {
          heading: 'Children',
          body: <p>Visuara is a business tool, is not directed at children, and requires users to be at least 18, as set out in the <Link href="/terms">Terms of Service</Link>. We do not knowingly collect account data from anyone under 16. If you believe a child has created an account, contact us and we will delete it.</p>,
        },
        {
          heading: 'Changes to this policy',
          body: <p>We may update this policy as the service changes. For a material change, we will update the effective date and email workspace owners before it takes effect.</p>,
        },
        {
          heading: 'Contact',
          body: <p>Questions or requests about privacy: <ContactLine email={CONTACT_EMAIL} />.</p>,
        },
      ]}
    />
  );
}
