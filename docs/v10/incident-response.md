# Incident response

## Severity
| Level | Examples | Target response |
|---|---|---|
| SEV1 | Cross-tenant data exposure; leaked credentials; public bucket; unauthorised delivery of a video | Immediately; contain within 1 hour |
| SEV2 | Platform down; renders failing for everyone; sign-in broken | Within 2 hours |
| SEV3 | One customer affected; degraded performance; email delays | Next business day |

## First 15 minutes (any severity)
1. Note the time and what was observed; open an incident log.
2. Check `/api/health/ready`, `/api/ops/metrics`, and `docker compose logs --since 30m api worker`.
3. Collect the `X-Request-ID` values from affected users' errors and search the logs for them.

## Containment playbooks
| Situation | Action |
|---|---|
| Leaked `CC_SIGNING_KEY` | Rotate it in `deploy/.env` and restart. All download links, sign-in codes and OAuth states become invalid; sessions stay (they are database tokens) |
| Leaked session or compromised account | Owner/admin suspends the member (ends sessions at once). Operator: `DELETE FROM sessions WHERE user_id=…` |
| Leaked R2 token | Revoke it in Cloudflare, create a new one, update `deploy/.env`, restart |
| Leaked Neon password | Reset the role password in Neon, update `DATABASE_URL`, restart |
| Leaked Google client secret | Rotate it in the Google console, update `deploy/.env`, restart |
| Bucket made public | Turn public access off in R2 now; then review R2 access logs, if available, for the exposure window |
| Unauthorised video in use | Creator revokes the licence or placement rights. Delivery and new links stop at once; issued links expire within 5 minutes |
| Abuse by one workspace (uploads or renders) | Lower its limits via environment and restart, or suspend its members; quotas already cap active jobs and storage |
| Bad deployment | `scripts/rollback.sh` |
| Database corruption or bad migration | Restore a Neon branch to a time before the incident ([backup-and-recovery.md](backup-and-recovery.md)) and point `DATABASE_URL` at it |

## After the incident
- Write a short report: timeline, impact (which workspaces and what data), root cause, fix, follow-ups.
- Notify affected customers when their data was exposed or their work lost. Personal-data breaches can carry legal notification deadlines (for example 72 hours under GDPR); confirm your obligations with your adviser.
- Add a regression test for the cause when it was a code defect.

## Contacts (fill in before launch)
| Role | Name | Contact |
|---|---|---|
| Incident lead / operator | | |
| Technical owner (backend) | | |
| Customer communications | | |
| Support address shown to customers | | |
