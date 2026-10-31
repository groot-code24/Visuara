import type { Metadata } from 'next';
import { ContactLine, LegalPage } from '@/components/legal';
import { CONTACT_EMAIL } from '@/lib/site';

export const metadata: Metadata = { title: 'Privacy Policy', description: 'What Context Commerce collects, why, and how long it keeps it.' };

export default function Privacy() {
  return (
    <LegalPage
      title="Privacy Policy"
      updated="31 October 2026"
      intro={<p>This policy explains what Context Commerce collects when you use the service, why we collect it, and the choices you have.</p>}
      sections={[
        {
          heading: 'What we collect',
          body: (
            <ul>
              <li><strong>Account details:</strong> your name and email address, and your Google account identifier if you sign in with Google. We do not use passwords.</li>
              <li><strong>Workspace details:</strong> your workspace name, its members and their roles, and invitations.</li>
              <li><strong>Your content:</strong> the videos and product images you upload, and what the service creates from them: keyframes, tracking data, previews, decisions and rendered videos.</li>
              <li><strong>Activity records:</strong> an audit log of actions such as approvals and downloads, and request logs for security and operations. Request logs leave out email addresses, tokens and query strings.</li>
            </ul>
          ),
        },
        {
          heading: 'How we use it',
          body: (
            <>
              <p>We use this information to sign you in, run the service you asked for, keep it secure, and contact you about your account.</p>
              <p>We do not sell your data, we do not show advertising, and we do not use your footage or product images to train AI models.</p>
            </>
          ),
        },
        {
          heading: 'Cookies',
          body: <p>We use only the cookies the service needs: a session cookie that keeps you signed in, and a security token that protects against forged requests. There are no analytics or advertising cookies.</p>,
        },
        {
          heading: 'Who else handles your data',
          body: (
            <>
              <p>We use these service providers to run Context Commerce. They process data only to provide their service to us:</p>
              <ul>
                <li>Vercel: hosts the website.</li>
                <li>Neon: hosts the database.</li>
                <li>Cloudflare: stores your media privately.</li>
                <li>Our email provider: delivers sign-in and invitation emails.</li>
                <li>Google: if you choose to sign in with Google.</li>
                <li>Our server hosting provider: runs the application and rendering.</li>
              </ul>
              <p>Other people see your content only if they are members of your workspace.</p>
            </>
          ),
        },
        {
          heading: 'How long we keep it',
          body: (
            <ul>
              <li>Your content and audit records stay for the life of your workspace, or until you ask us to delete them.</li>
              <li>Expired sessions and sign-in codes are removed within about a day.</li>
              <li>Abandoned uploads are deleted after about an hour.</li>
              <li>When a member is removed, their email address is replaced at once and their sessions are deleted. A record of the change stays in the audit log.</li>
            </ul>
          ),
        },
        {
          heading: 'Security',
          body: <p>Media is kept in private storage and is available only after an access check, through links that expire in minutes. Sign-in codes and session tokens are stored only in hashed form. Every workspace is isolated from every other.</p>,
        },
        {
          heading: 'Your choices and rights',
          body: <p>You can ask us for a copy of your data, or ask us to correct or delete it, by writing to <ContactLine email={CONTACT_EMAIL} /> from your account email address. Depending on where you live, you may have further rights, including the right to complain to a data protection authority.</p>,
        },
        {
          heading: 'Changes',
          body: <p>If we make significant changes to this policy, we will update the date above and tell workspace owners by email.</p>,
        },
        {
          heading: 'Contact',
          body: <p>Questions about privacy: <ContactLine email={CONTACT_EMAIL} />.</p>,
        },
      ]}
    />
  );
}
