# Output Contract

Each run creates one project folder under the selected output root:

```text
{output_root}/{project_slug}/
  inputs/
  outputs/
```

The run folder is server-side storage. The frontend should display links returned by the API rather than assuming direct filesystem access.

## Folder Structure

```text
outputs/
  comp_study_deck.pptx
  data/
    candidate_comps.json
    approved_comps.json
    enriched_comps.json
    repaired_comps.json
  graphics/
  images/
    comp_01_hero_01.jpg
    comp_01_hero_02.jpg
    comp_01_hero_03.jpg
  json/
    deck_data.json
    deck_strategy.json
    approved_comps_normalized.json
    source_metadata.json
    diligence_notes.json
    repair_notes.json
    deck_audit.json
    field_repair_tasks.json
    field_repair_results.json
    image_manifest.json
  sources/
  working/
```

## Primary Client Artifact

`outputs/comp_study_deck.pptx`

This is the client-facing deck. The current deck order is:

1. Cover
2. Comp Summary Matrix
3. One profile slide per approved comp
4. Features and Amenities Matrix
5. Project Positioning Takeaways

## Important JSON Artifacts

| File | Purpose |
| --- | --- |
| `outputs/json/deck_data.json` | Full normalized data object used to generate the PPTX. |
| `outputs/json/deck_strategy.json` | Dynamic slide strategy, including summary columns, matrix columns, and adaptive facts. |
| `outputs/json/approved_comps_normalized.json` | Approved comps transformed into the deck schema. |
| `outputs/json/source_metadata.json` | Source trail used for comp facts and slide footers. |
| `outputs/json/diligence_notes.json` | Internal warnings, missing data, confidence notes, and selection reasoning. |
| `outputs/json/deck_audit.json` | Final quality gate results before PPTX generation. |
| `outputs/json/field_repair_tasks.json` | Specific missing deck-facing facts selected for targeted repair. |
| `outputs/json/field_repair_results.json` | Results of capped field-level repair searches. |
| `outputs/json/image_manifest.json` | Image validation, dedupe, rejection reasons, repair attempts, and final image counts. |

## Frontend Output Display

At minimum, the frontend should expose:

- PPTX download/open link.
- Job status and any terminal error.
- Optional links to `deck_data.json`, `deck_audit.json`, and `diligence_notes.json` for internal review.

Do not show diligence notes or source debugging inside the client-facing deck by default.
