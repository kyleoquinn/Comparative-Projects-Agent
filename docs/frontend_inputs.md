# Frontend Inputs

The frontend should collect enough information to let the Comp Agent understand the subject project, discover candidate comps, and tune the deck strategy. The UI can be polished separately; this document is the functional input contract.

## Required Inputs

| Field | Type | Notes |
| --- | --- | --- |
| `address` | string | Subject project address or best available location. |
| `program_type` | string | Plain-language project focus, such as `office lobby repositioning`, `residential tower`, `civic landmark`, or `hotel conversion`. |
| `geography` | string | Search market or region, such as `New York City`, `Northeast US`, or `global`. |

## Recommended Inputs

| Field | Type | Notes |
| --- | --- | --- |
| `project_name` | string | Optional display name. If omitted, address can drive the deck subtitle. |
| `max_comps` | number | Target number of candidate comps to return for approval. |
| `comp_types` | string or string[] | Comparable lanes, such as `podium renovation`, `adaptive reuse`, `public realm`, `luxury residential`. |
| `amenity_priorities` | string or string[] | Design or program priorities. These are used for discovery, deck strategy, adaptive facts, and the matrix. |
| `radius_miles` | number | Optional geographic radius. |
| `time_horizon_years` | number | Optional completion/status time window. |
| `user_defined_comps` | string | Newline-separated user comps. See format below. |
| `auto_approve_user_comps` | boolean | If true, user-defined comps can be preselected in the approval UI. |
| `output_root` | string | Server-side output root. Use a backend-approved path in production. |

## User-Defined Comps Format

The temporary UI accepts newline-separated rows:

```text
660 Fifth Avenue | New York, NY | Office lobby precedent
343 Madison | New York, NY | Office tower repositioning precedent
```

The backend treats these as starter hints only. Approved user-defined comps still go through enrichment, repair, image validation, audit, and deck generation.

## Input Behavior

- Use placeholder text in the frontend rather than hardcoded sample values.
- Keep input language natural. The backend is designed to reason from broad project intent.
- Do not expose research call budgets or repair settings in the first production UI.
- Let the user approve comps before the expensive enrichment/deck generation stage.

## Candidate Approval UI

Discovery returns candidate objects with:

- `comp_id`
- `comp_name`
- `location`
- `comp_type`
- `relevance_score`
- `candidate_source`
- `known_attributes.presentation_takeaway`

The frontend should show candidates as selectable rows or cards. On approval, send selected `comp_id` values to the approval endpoint.

## Progress Labels

Use these progress messages during the approval/deck-generation stage:

- Saving approvals
- Enriching approved comps
- Repairing incomplete comp packages
- Validating image packages
- Auditing deck completeness
- Running targeted field repairs
- Writing Comp Study Deck

Discovery can use simpler labels such as:

- Reading project brief
- Searching comparable projects
- Preparing candidate comps
- Ready for approval
