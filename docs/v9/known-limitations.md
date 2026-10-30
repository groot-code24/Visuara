# Known limitations (V9)

V8 limitations still apply unless listed as resolved ([docs/v8/known-limitations.md](../v8/known-limitations.md)).

## Resolved in V9
- **PostgreSQL and object storage:** implemented, with Neon via `DATABASE_URL` and S3-compatible storage via `CC_S3_*`. Both are tested locally (PostgreSQL 16, an S3 mock).
- **Vulnerable image decoder:** Pillow 13 on the supported Python 3.11+ runtime. `pip-audit` reports no known vulnerabilities.
- **Sign-up:** brands and creators can join without a password or an invitation (Google, or an email code or magic link).

## Evidence
- **No real footage has been evaluated.** The real-footage protocol and tooling exist and are tested, but have only run on clips marked synthetic.
- **No live integrations.** Neon, Google sign-in, SMTP delivery and a real bucket have not been exercised; no credentials were available here. The code paths are tested against local stand-ins:
  - an embedded PostgreSQL 16 server;
  - a stubbed Google token exchange;
  - a file outbox for email;
  - moto for S3.
- **The Docker image has not been built** (no Docker daemon on this machine).

## 3D scene check
- **No measurable improvement.** On 72 synthetic clips it has the same number of false rejections as the V8 check (1 each, on different clips). Its gains are explanation (per-pixel cause), occlusion detection (2 of 6 static-occluder clips) and stronger motion evidence. It is not a measured reduction in false rejections.
- **Known false-rejection mode (held-out, topdown s5):** the 1 px plane tolerance ignores pose error. This is unfixed in this release; see [3d-scene-check.md](3d-scene-check.md).
- **Static occluders over the tracked surface** degrade the camera solve and are rejected for that reason, not as `OBJECT_OCCLUDED`.
- **Untextured occluders** are not detected. Objects moving exactly like static scene points (along epipolar lines at the matching speed) are indistinguishable from them.
- **Cost:** about 1.5–3.5 s per 61-frame clip on top of the V8 analysis.

## Authentication
- **No email ownership check beyond possession of the code or link.** Corporate SSO (SAML/OIDC for a brand's domain) is not supported.
- **Sessions are not revoked when a Google identity is linked** to an existing password account.
- **Account recovery depends on email access.** Password reset by email is not implemented for password accounts; those users can sign in with an email code instead, as both paths resolve to the same account.
- **Brand and creator roles are fixed per account.** One person needs two accounts (two emails) to act in both roles.

## Platform
- **Neon latency.** Each request makes several database round trips, so deploy in the Neon project's region. The pool is per process (`CC_DB_POOL_SIZE`).
- **Rate limiter.** It lives in the database (works across hosts, but adds writes); a dedicated store (Redis) or gateway limiter is preferable at scale.
- **Local cache.** Media restored from the bucket stays on local disk without eviction. Add a cache-cleanup job before running on small disks.
- **Payments.** Not processed. Agency workspaces and ad-platform measurement are not built.
