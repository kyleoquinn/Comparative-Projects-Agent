# Frontend Inputs

The frontend should collect enough information to let the Comp Agent understand the subject project, discover candidate comps, and tune the deck strategy. Manual entry and RFP autofill should fill the same project brief fields.

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
| `scope_summary` | string | Plain-language summary of the work being studied. |
| `design_priorities` | string or string[] | Design or business priorities, such as arrival experience, tenant amenities, public realm, or retail activation. |
| `max_comps` | number | Target number of candidate comps to return for approval. |
| `radius_miles` | number | Optional geographic radius. |
| `time_horizon_years` | number | Optional completion/status time window. |
| `output_root` | string | Server-side output root. Use a backend-approved path in production. |

## Comparative Projects Inputs

The production frontend should send comp-specific guidance inside `comparative_projects`.

| Field | Type | Notes |
| --- | --- | --- |
| `comparative_projects.comp_guidance` | string | Natural-language steering for what makes a good comp. |
| `comparative_projects.comp_types` | string[] | Comparable lanes, such as `podium renovation`, `adaptive reuse`, `public realm`, or `luxury residential`. |
| `comparative_projects.must_include_comps` | object[] | Comps the user wants included in the approval list. |

## Must-Use Comps Format

The proof-of-concept UI accepts newline-separated rows:

```text
660 Fifth Avenue | New York, NY | Office lobby precedent
343 Madison | New York, NY | Office tower repositioning precedent
```

The future frontend can send the same information as structured objects:

```json
[
  {
    "name": "660 Fifth Avenue",
    "location": "New York, NY",
    "note": "Office lobby precedent"
  }
]
```

Must-use comps are added to the agent's suggested candidate list, duplicates are merged, and the user still approves the final list before deeper research.

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
