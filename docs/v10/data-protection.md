# Data protection

This document records what the software does. It is not legal advice; have a qualified adviser review your terms, privacy notice and processing agreements for the countries you operate in.

## Data held
| Data | Where | Purpose |
|---|---|---|
| Name, email, password hash (PBKDF2-SHA256, 180k iterations), Google subject id | Database | Accounts and sign-in |
| Session tokens, sign-in challenges, OAuth state | Database (hashed) and HttpOnly cookies | Authentication |
| Workspace, membership role, invitations | Database | Access control |
| Source videos, product images, 3D models, keyframes, tracks, renders | R2 (private) and the VM's local cache | The service itself |
| Offers, licences, approvals, consent records, payment ledger | Database | Contract and audit trail |
| Events (audit log), queue events | Database | Security and operations |
| Request logs (no emails, tokens or query strings) | VM stdout, Docker log rotation (5 × 20 MB per container) | Operations |

## Private by default
- The bucket is private. All media is served only after an access check, through presigned URLs valid for 5 minutes.
- Source footage is never shown in the marketplace (only keyframes) and never delivered to a brand. Brands receive only renders of approved, consented, licensed placement versions.
- No analytics, advertising or third-party scripts run in the frontend. CSP restricts connections to the API and https endpoints.

## Retention (pilot defaults)
| Data | Retention |
|---|---|
| Expired sessions, sign-in challenges (after 1 day), pending sign-ups, rate-limit rows (after 1 day) | Pruned by maintenance every 6 h |
| Abandoned upload sessions | Expire after 1 h; their objects are deleted |
| Local media cache | Evicted after 72 h idle when the bucket holds the file |
| Media, licences, consent, payment ledger, audit events | Kept for the life of the workspace (they evidence rights and consent) |
| Removed members | Email replaced by a placeholder at once; sessions and Google link deleted; the record stays for the audit trail |
| Logs | As long as Docker rotation keeps them (about 100 MB per container) |

## Deletion process (operator, on a verified request)
1. Confirm the request comes from the workspace owner (reply from the account email).
2. Revoke the workspace's active licences (the UI or API), so delivery stops at once.
3. Delete the media objects by key prefix for the workspace's files (list them via the videos, products and jobs tables), then the rows.

   **Not automated in V10:** there is no "delete workspace" endpoint. This is a known limitation, and the operator runs it with SQL and the S3 API.
4. Keep the consent, licence and payment records only where law or contract requires it, and record why.

## Subprocessors you will configure
Vercel (static hosting), Neon (database), Cloudflare (R2 storage; Tunnel if used), your SMTP provider, Google (sign-in), and your VM provider. List them in your privacy notice.
