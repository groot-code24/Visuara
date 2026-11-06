# Tenant isolation report

**4 November 2026.** Every result below comes from automated tests that run on SQLite and PostgreSQL 16. Each is listed with its test.

## What was tested
| Boundary | Test | Result |
|---|---|---|
| A publisher's opportunities, analyses, scenes, rights, video versions, inventory previews, and a brand's campaigns, reports and requests, read by a stranger publisher | `test_security_marketplace.py::test_other_organizations_see_nothing` | 404 on every path |
| Review, withdraw, list, approve, reject, release and cancel-analysis by a stranger | same | 404 on every action |
| A rival brand: reserved item, release, cancel, campaign | same | 404 |
| The publisher reading the brand's campaign; each party acting for the other (cancel, accept) | same | 404; `WRONG_PARTY` |
| Viewer and editor limits at a brand; managers alone change campaign state | `test_roles_and_kinds_gate_marketplace_actions` | 403 `ROLE_FORBIDDEN` |
| A direct organization using the marketplace; changing kind after marketplace activity | same | 403 `ORGANIZATION_KIND_FORBIDDEN`; 409 `KIND_LOCKED` |
| Concurrent reservations by two brands | `test_marketplace.py::test_only_one_brand_wins_a_reservation` | Exactly one 200, one 409 `INVENTORY_CONFLICT` |
| Products and media across organizations after switching | `test_orgs.py::test_a_person_creates_and_switches_between_organizations` | Empty list; 404 |
| Role per membership (viewer elsewhere, owner at home) | `test_existing_accounts_accept_invitations_and_get_that_role` | 403 where viewer |
| Suspension in one organization only | `test_suspension_and_removal_are_per_organization` | Home still works; switching refused |
| Opportunities pointing at another organization's scene | `test_foundations.py::test_an_opportunity_cannot_point_at_another_organizations_scene` | Refused |
| Analyses of another organization | `test_intelligence.py::test_analyses_are_private_to_their_organization` | 404 |
| Platform administration without the flag | `test_orgs.py::test_platform_administration_is_granted_outside_the_api`, `test_marketplace.py::test_listing_needs_review_and_verified_rights` | 403 `PLATFORM_ADMIN_REQUIRED` |
| Forged download links | `test_adversarial_input_is_refused_or_stored_inert` | 403 |
| Ledger changes after the fact | `test_marketplace.py::test_the_ledger_is_append_only_and_balanced` | Refused by the database |
| The isolation tests from before Phase 1 | `test_workflow.py::test_org_isolation`, `test_platform.py::test_member_roles_gate_actions_server_side` | Still pass |

## Findings fixed during the build
- **Signed media checked the wrong organization.** `/api/media/{token}` used the role stored in `users`, which belongs to the default organization. A member working in their second organization was refused, and the role checked was the wrong one. It now uses the membership in the job's organization.
- **Decisions acted in the wrong organization.** The decision engine took the creator's default organization for event logging. It now acts in the decision's own organization.

## Not covered by automated tests
- Load and timing attacks.
- A real multi-host deployment: rate limits sit in the database, and the live site is down (B-2).
- The web interface's own access logic is covered only by the backend checks it relies on.
