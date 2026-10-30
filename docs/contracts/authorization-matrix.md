# Authorization matrix

All resource endpoints require a session and look resources up by ID **and** the caller's organization (cross-organization access returns 404). Mutations require the double-submit CSRF header.

| Action | Brand member | Creator: rights holder (uploaded the video) | Creator: other member |
|---|---|---|---|
| Create product | yes | no | no |
| Upload video / analyse scenes | no | yes | yes (own videos) |
| Create placement / revise (new version) | yes | no | no |
| Run quality check (DRAFT) | yes | yes | yes |
| Fit review (DRAFT) | yes | yes | yes |
| Propose | yes (quality PASSED and fit ACCEPTED) | no | no |
| Grant or change consent (DRAFT/PROPOSED) | change terms only; changing terms clears consent | yes | **no** (`NOT_RIGHTS_HOLDER`) |
| Creator approval | no | yes (consent active) | **no** |
| Brand approval | yes (after creator) | no | no |
| Reject (PROPOSED/CREATOR_APPROVED) | yes | yes | no |
| Re-run verification / render / cancel job | yes | no | no |
| Revoke rights | yes | yes | no |
| Download asset | yes, if all delivery gates pass | yes, same | yes, same |
| Create tracked link | yes (RENDERED/DELIVERED, licensed channel) | no | no |

Delivery gates (re-checked on every download): job SUCCEEDED; job version = current version; placement RENDERED or DELIVERED; output validation PASSED; consent active and unexpired; video licence valid; approvals bound to the current content hash; analysis and track integrity; optional `?channel=` licensed.

Known gap: brands and creators must belong to the same organization (joined by invitation). Cross-organization licence grants are not modelled.
