# Stage Contracts

This document defines the **stable interface** of the Comp Agent backend. The frontend orchestrator and any sibling backend agents depend on these contracts. Treat them as load-bearing — changes here ripple outward.

All stages are methods on `CompAppStages` (`src/comp_agent/stages.py`) and operate on a `ProjectBrief` (`src/comp_agent/models.py`). Each stage writes files into `projects/<project_slug>/` and returns a `dict[str, Path]` mapping logical names to absolute paths.

## Workspace layout

```
projects/<project_slug>/
├── inputs/              # The brief and source-selection toggles as supplied
├── data/                # Intermediate JSON/CSV — readable by downstream stages
│   └── raw_research/    # Per-comp extracted facts (one JSON per comp_id)
├── graphics/            # SVG charts (readiness, coverage, metrics)
├── sources/             # Archived source manifest
└── outputs/             # Client-facing deliverables
    ├── images/          # Downloaded hero images + manifest
    ├── json/            # Normalized JSON artifacts for the frontend
    └── comp_study_deck.pptx
```

## Stage 1 — `discover(brief, source_selection=None)`

**Reads:** the brief argument only.
**Writes:**

| Key | Path | Purpose |
|---|---|---|
| `project_brief` | `inputs/project_brief.json` | Canonical brief snapshot |
| `source_selection` | `inputs/source_selection.json` | Public-only vs. licensed-source toggles |
| `comp_criteria` | `data/comp_criteria.json` | Research criteria derived from brief |
| `candidate_comps` | `data/candidate_comps.json` | List of `CompCandidate` (mutated by `research`) |
| `candidate_comps_csv` | `data/candidate_comps.csv` | Same, flattened |
| `source_log` | `data/source_log.json` | List of `SourceLogEntry` (mutated by `research`) |
| `source_query_plan` | `data/source_query_plan.csv` | Planned search queries |
| `sources_manifest` | `sources/manifest.json` | Archived source metadata |

**Side effects:** May call OpenAI Responses API web_search if a search provider is configured. Falls back to placeholder candidates if not.

## Stage 2 — `approve(brief, approved_ids=None, *, limit=1, notes=...)`

**Reads:** `data/candidate_comps.json` (runs `discover` if missing).
**Writes:**

| Key | Path |
|---|---|
| `approved_comps` | `data/approved_comps.json` (list of `ApprovedComp`) |
| `approval_decisions` | `data/approval_decisions.json` (list of `ApprovalDecision`) |
| `decision_log` | `data/decision_log.json` (list of `UserDecision`) |

**Selection rule:** if `approved_ids` is provided, those candidates are approved; otherwise the first `limit` candidates are auto-approved (POC mode). The frontend should always pass explicit IDs.

## Stage 3 — `research(brief)`

**Reads:** `data/approved_comps.json` (runs `approve` if missing), `data/candidate_comps.json`, `data/source_log.json`.
**Writes:**

| Key | Path |
|---|---|
| `raw_research_folder` | `data/raw_research/` (one `<comp_id>.json` per approved comp) |
| `enriched_comps` | `data/enriched_comps.json` (per-comp pre-repair snapshot) |
| `repaired_comps` | `data/repaired_comps.json` (per-comp post-repair snapshot) |
| `repair_notes` | `outputs/json/repair_notes.json` (which fields were missing before/after) |
| `comp_records_json` | `data/comp_records.json` (list of `CompRecord`) |
| `extracted_facts` | `data/extracted_facts.json` (flat list of `ExtractedFact`) |

**Mutates in place:** `data/candidate_comps.json` and `data/source_log.json` are rewritten with enrichment results.

## Stage 4 — `format_outputs(brief)`

**Reads:** `data/comp_records.json` (runs `research` if missing).
**Writes:** CSV/JSON pivots used by the deck and downstream consumers.

| Key | Path |
|---|---|
| `comp_records_csv` | `data/comp_records.csv` |
| `comparison_matrix_csv` | `data/comparison_matrix.csv` |
| `amenity_matrix_csv` | (alias of comparison matrix) |
| `scale_comparison_csv` | `data/scale_comparison.csv` |
| `presentation_cards` | `data/presentation_cards.json` |

## Stage 5 — `generate_outputs(brief)`

**Reads:** approved comps, candidate comps, comp records, source log.
**Writes:** the client-facing deliverables.

| Key | Path |
|---|---|
| `comp_study_deck` / `poc_deck` | `outputs/comp_study_deck.pptx` |
| `deck_data` | `outputs/json/deck_data.json` |
| `deck_strategy` | `outputs/json/deck_strategy.json` |
| `approved_comps_normalized` | `outputs/json/approved_comps_normalized.json` |
| `source_metadata` | `outputs/json/source_metadata.json` |
| `diligence_notes` | `outputs/json/diligence_notes.json` |
| `deck_audit` | `outputs/json/deck_audit.json` |
| `field_repair_tasks` | `outputs/json/field_repair_tasks.json` |
| `field_repair_results` | `outputs/json/field_repair_results.json` |
| `approved_comp_readiness_chart` | `graphics/approved_comp_readiness.svg` |
| `source_coverage_chart` | `graphics/source_coverage.svg` |
| `metric_snapshot_chart` | `graphics/metric_snapshot.svg` |
| `output_manifest` | `data/output_manifest.json` (flat string map of all of the above) |

**Side effects:** Downloads hero images into `outputs/images/`; runs a bounded field-repair loop (capped by `COMP_AGENT_FIELD_REPAIR_LIMIT`, default 4).

## Stage 6 — `audit(brief)`

**Reads:** `data/comp_records.json`.
**Writes:**

| Key | Path |
|---|---|
| `audit_report` | `data/audit_report.json` (list of `ReviewFlag`) |
| `review_flags_csv` | `data/review_flags.csv` |
| `revision_tasks` | `data/revision_tasks.json` (list of `RevisionTask`) |

This audit is record-level (different from the field-level `deck_audit.json` produced inside `generate_outputs`).

## Convenience — `run_poc(brief, source_selection=None)`

Runs all six stages in order and writes a flat string-keyed `data/poc_manifest.json` mapping every output key to its path. Returns the same dict as a `dict[str, str]`.

## Rules for changing this contract

1. **Additive changes are safe.** Adding a new key to a returned dict, a new field to a dataclass, or a new file in `outputs/json/` won't break consumers.
2. **Renames and removals are breaking.** They require a coordinated update across this repo, the frontend, and any sibling agents. Don't do them silently.
3. **The keys in the returned `dict[str, Path]` are part of the contract** — the frontend uses them to locate artifacts. Treat them like API field names.
4. **File paths are part of the contract.** Moving `deck_data.json` from `outputs/json/` to `outputs/` would break consumers even if the key name stays the same.
5. **When in doubt, add — don't replace.** If a new structure is needed, write a new file alongside the old one and deprecate the old one in a later pass.
