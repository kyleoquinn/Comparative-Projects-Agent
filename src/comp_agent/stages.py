from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from comp_agent.charts import create_comp_readiness_chart, create_metric_snapshot, create_source_coverage_chart
from comp_agent.deck import create_concept_deck, create_concept_deck_from_data
from comp_agent.deck_data import build_comp_study_deck_data
from comp_agent.images import download_hero_images
from comp_agent.models import (
    ApprovalDecision,
    ApprovedComp,
    CompCandidate,
    CompRecord,
    ExtractedFact,
    ProjectBrief,
    ReviewFlag,
    RevisionTask,
    SourceLogEntry,
    SourceSelection,
    UserDecision,
)
from comp_agent.research import CompResearchAgent
from comp_agent.sources import archive_source_documents
from comp_agent.workspace import ProjectWorkspace, slugify, write_csv, write_json


DEFAULT_RESEARCH_CONCURRENCY = 3


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _candidate_from_dict(payload: dict[str, Any]) -> CompCandidate:
    return CompCandidate(
        comp_id=str(payload["comp_id"]),
        comp_name=str(payload["comp_name"]),
        location=str(payload["location"]),
        comp_type=str(payload["comp_type"]),
        relevance_score=int(payload["relevance_score"]),
        status=str(payload["status"]),
        known_attributes=dict(payload.get("known_attributes") or {}),
        missing_attributes=list(payload.get("missing_attributes") or []),
        source_notes=list(payload.get("source_notes") or []),
    )


def _source_log_from_dict(payload: dict[str, Any]) -> SourceLogEntry:
    return SourceLogEntry(
        source_name=str(payload["source_name"]),
        source_type=str(payload["source_type"]),
        url_or_search=str(payload["url_or_search"]),
        related_output=str(payload["related_output"]),
        status=str(payload.get("status", "planned")),
        retrieved_at=str(payload.get("retrieved_at", "")),
        notes=str(payload.get("notes", "")),
    )


class CompAppStages:
    def __init__(self, output_root: str | Path = "projects") -> None:
        self.output_root = Path(output_root)
        self.agent = CompResearchAgent()

    def workspace_for(self, brief: ProjectBrief) -> ProjectWorkspace:
        return ProjectWorkspace(self.output_root, brief.project_name).create()

    def discover(self, brief: ProjectBrief, source_selection: SourceSelection | None = None) -> dict[str, Path]:
        selection = source_selection or SourceSelection.public_only()
        workspace = self.workspace_for(brief)
        criteria = self.agent.build_criteria(brief)
        queries = self.agent.build_source_queries(brief)
        candidates = self.agent.identify_candidates(brief)
        source_log = self.agent.build_source_log(queries)
        sources_manifest = archive_source_documents(source_log, workspace.sources)

        paths = {
            "project_brief": write_json(workspace.inputs / "project_brief.json", brief),
            "source_selection": write_json(workspace.inputs / "source_selection.json", selection),
            "comp_criteria": write_json(workspace.data / "comp_criteria.json", criteria),
            "candidate_comps": write_json(workspace.data / "candidate_comps.json", candidates),
            "source_log": write_json(workspace.data / "source_log.json", source_log),
            "sources_manifest": sources_manifest,
            "source_query_plan": write_csv(
                workspace.data / "source_query_plan.csv",
                [asdict(item) for item in queries],
                ["topic", "query", "target_source_type", "why_it_matters"],
            ),
            "candidate_comps_csv": write_csv(
                workspace.data / "candidate_comps.csv",
                [
                    {
                        **asdict(item),
                        "known_attributes": "; ".join(f"{key}: {value}" for key, value in item.known_attributes.items()),
                        "missing_attributes": "; ".join(item.missing_attributes),
                        "source_notes": "; ".join(item.source_notes),
                    }
                    for item in candidates
                ],
                [
                    "comp_id",
                    "comp_name",
                    "location",
                    "comp_type",
                    "relevance_score",
                    "status",
                    "known_attributes",
                    "missing_attributes",
                    "source_notes",
                ],
            ),
        }
        return paths

    def approve(
        self,
        brief: ProjectBrief,
        approved_ids: list[str] | None = None,
        *,
        limit: int | None = 1,
        notes: str = "POC approval",
    ) -> dict[str, Path]:
        workspace = self.workspace_for(brief)
        candidate_path = workspace.data / "candidate_comps.json"
        if not candidate_path.exists():
            self.discover(brief)
        candidates = [_candidate_from_dict(item) for item in _load_json(candidate_path)]
        id_set = set(approved_ids or [])
        selected = [item for item in candidates if item.comp_id in id_set] if id_set else candidates[: limit or len(candidates)]
        approved = [
            ApprovedComp(
                comp_id=item.comp_id,
                comp_name=item.comp_name,
                location=item.location,
                comp_type=item.comp_type,
                approval_notes=notes,
            )
            for item in selected
        ]
        decisions = [
            ApprovalDecision(comp_id=item.comp_id, decision="approved", notes=notes)
            for item in selected
        ]
        decision_log = [
            UserDecision(
                decision_id=slugify(f"approve-{item.comp_id}"),
                target_type="candidate_comp",
                target_id=item.comp_id,
                decision="approved",
                notes=notes,
            )
            for item in selected
        ]
        return {
            "approved_comps": write_json(workspace.data / "approved_comps.json", approved),
            "approval_decisions": write_json(workspace.data / "approval_decisions.json", decisions),
            "decision_log": write_json(workspace.data / "decision_log.json", decision_log),
        }

    def research(self, brief: ProjectBrief) -> dict[str, Path]:
        workspace = self.workspace_for(brief)
        approved_path = workspace.data / "approved_comps.json"
        if not approved_path.exists():
            self.approve(brief, limit=1)
        approved_items = _load_json(approved_path)

        raw_folder = workspace.data / "raw_research"
        raw_folder.mkdir(parents=True, exist_ok=True)
        candidate_path = workspace.data / "candidate_comps.json"
        candidate_map: dict[str, CompCandidate] = {}
        if candidate_path.exists():
            candidate_map = {_candidate_from_dict(item).comp_id: _candidate_from_dict(item) for item in _load_json(candidate_path)}
        source_log_path = workspace.data / "source_log.json"
        source_log_items = _load_json(source_log_path) if source_log_path.exists() else []
        search_provider = self.agent.search_provider

        def approved_candidate(item: dict[str, Any]) -> CompCandidate:
            comp_id = str(item["comp_id"])
            return candidate_map.get(comp_id) or CompCandidate(
                comp_id=comp_id,
                comp_name=str(item["comp_name"]),
                location=str(item["location"]),
                comp_type=str(item["comp_type"]),
                relevance_score=70,
                status="needs_research",
                known_attributes={"program_type": brief.program_type},
                missing_attributes=["public source verification"],
                source_notes=["Approved candidate was missing from candidate_comps.json."],
            )

        def candidate_record_and_facts(candidate: CompCandidate) -> tuple[CompRecord, list[ExtractedFact]]:
            attrs = candidate.known_attributes
            candidate_sources = attrs.get("_sources") if isinstance(attrs.get("_sources"), list) else []
            source_count = len(candidate_sources)
            total_sf = attrs.get("total_sf") or attrs.get("gross_area") or attrs.get("rentable_area")
            try:
                total_sf = int(total_sf) if total_sf not in (None, "") else None
            except (TypeError, ValueError):
                total_sf = None
            record = CompRecord(
                comp_id=candidate.comp_id,
                project_name=candidate.comp_name,
                location=candidate.location,
                program_type=str(attrs.get("program_type") or brief.program_type),
                total_sf=total_sf,
                status="source_snapshot" if source_count else "needs_research",
                completion_year=str(attrs.get("completion_year") or attrs.get("status_year") or ""),
                developer_owner=str(attrs.get("developer_owner") or ""),
                architect_designer=str(attrs.get("architect_designer") or ""),
                amenities=[],
                relevance_summary=str(attrs.get("relevance_to_subject") or attrs.get("presentation_takeaway") or f"Approved precedent for {brief.program_type}."),
                confidence="medium" if source_count else "low",
                source_count=source_count,
                review_notes=[
                    *candidate.source_notes,
                    "Created from approved-comp evidence package; source facts should be spot-checked before final client use.",
                ],
            )
            facts = [
                ExtractedFact(
                    comp_id=record.comp_id,
                    field="evidence_package",
                    value=record.project_name,
                    source_type="openai_web_search" if source_count else "approved_candidate",
                    source_name="Approved comp evidence package",
                    confidence=record.confidence,
                    human_review_required=True,
                    notes="Candidate and attributes extracted from staged enrichment/repair flow.",
                )
            ]
            for source in candidate_sources:
                if not isinstance(source, dict):
                    continue
                facts.append(
                    ExtractedFact(
                        comp_id=record.comp_id,
                        field="source",
                        value=source.get("notes") or source.get("name") or source.get("url"),
                        source_type=str(source.get("type") or "public web"),
                        source_url=str(source.get("url") or ""),
                        source_name=str(source.get("name") or source.get("title") or ""),
                        confidence="medium",
                        human_review_required=True,
                        notes="Source supplied by evidence package output.",
                    )
                )
            return record, facts

        def research_approved_item(index: int, item: dict[str, Any]) -> dict[str, Any]:
            comp_id = str(item["comp_id"])
            candidate = approved_candidate(item)
            working_candidate = candidate
            enrichment_warnings: list[str] = []
            repair_warnings: list[str] = []

            if search_provider:
                try:
                    enrichment = search_provider.enrich_candidate(brief, candidate)
                except Exception as error:
                    enrichment = None
                    enrichment_warnings.append(str(error))
                if enrichment:
                    enrichment_warnings.extend(enrichment.warnings)
                    if enrichment.candidates:
                        working_candidate = _merge_candidate_evidence(candidate, enrichment.candidates[0])
                        working_candidate.status = "source_snapshot"
                    elif enrichment.warnings:
                        working_candidate.source_notes.extend(f"Live enrichment warning: {warning}" for warning in enrichment.warnings)
            elif working_candidate.status != "source_snapshot":
                working_candidate.source_notes.append("Live enrichment unavailable; retaining approved discovery data.")

            enriched_snapshot = _candidate_snapshot(working_candidate, stage="enriched", warnings=enrichment_warnings)

            missing_before = _candidate_missing_fields(working_candidate)
            repair_attempted = bool(search_provider and missing_before)
            if repair_attempted and search_provider:
                try:
                    repair = search_provider.repair_candidate(brief, _candidate_snapshot(working_candidate, stage="pre_repair"), missing_before)
                except Exception as error:
                    repair = None
                    repair_warnings.append(str(error))
                if repair:
                    repair_warnings.extend(repair.warnings)
                    if repair.candidates:
                        working_candidate = _merge_candidate_evidence(working_candidate, repair.candidates[0])
                        working_candidate.status = "source_snapshot"
                    elif repair.warnings:
                        working_candidate.source_notes.extend(f"Live repair warning: {warning}" for warning in repair.warnings)

            missing_after = _candidate_missing_fields(working_candidate)
            repaired_snapshot = _candidate_snapshot(working_candidate, stage="repaired", warnings=repair_warnings, missing_fields=missing_after)
            repair_note = {
                "comp_id": comp_id,
                "project_name": working_candidate.comp_name,
                "missing_fields_before_repair": missing_before,
                "missing_fields_after_repair": missing_after,
                "repair_attempted": repair_attempted,
                "repair_warnings": repair_warnings,
            }
            record, facts = candidate_record_and_facts(working_candidate)
            return {
                "index": index,
                "comp_id": comp_id,
                "candidate": working_candidate,
                "enriched_snapshot": enriched_snapshot,
                "repaired_snapshot": repaired_snapshot,
                "repair_note": repair_note,
                "record": record,
                "facts": facts,
                "source_log": [
                    *(_safe_source_log(enrichment) if "enrichment" in locals() and enrichment else []),
                    *(_safe_source_log(repair) if "repair" in locals() and repair else []),
                ],
            }

        results = _run_parallel_research(approved_items, research_approved_item, _research_concurrency())

        records: list[CompRecord] = []
        facts_by_comp: dict[str, list[ExtractedFact]] = {}
        enriched_snapshots: list[dict[str, Any]] = []
        repaired_snapshots: list[dict[str, Any]] = []
        repair_notes: list[dict[str, Any]] = []
        for result in results:
            comp_id = result["comp_id"]
            working_candidate = result["candidate"]
            candidate_map[comp_id] = working_candidate
            source_log_items.extend(result["source_log"])
            enriched_snapshots.append(result["enriched_snapshot"])
            repaired_snapshots.append(result["repaired_snapshot"])
            repair_notes.append(result["repair_note"])
            record = result["record"]
            facts = result["facts"]
            records.append(record)
            facts_by_comp[record.comp_id] = facts
            write_json(raw_folder / f"{record.comp_id}.json", facts)

        if candidate_path.exists():
            write_json(candidate_path, [asdict(candidate) for candidate in candidate_map.values()])
        if source_log_path.exists():
            write_json(source_log_path, source_log_items)

        return {
            "raw_research_folder": raw_folder,
            "enriched_comps": write_json(workspace.data / "enriched_comps.json", enriched_snapshots),
            "repaired_comps": write_json(workspace.data / "repaired_comps.json", repaired_snapshots),
            "repair_notes": write_json(workspace.outputs / "json" / "repair_notes.json", repair_notes),
            "comp_records_json": write_json(workspace.data / "comp_records.json", records),
            "extracted_facts": write_json(workspace.data / "extracted_facts.json", [fact for facts in facts_by_comp.values() for fact in facts]),
        }

    def format_outputs(self, brief: ProjectBrief) -> dict[str, Path]:
        workspace = self.workspace_for(brief)
        records_path = workspace.data / "comp_records.json"
        if not records_path.exists():
            self.research(brief)
        records = _load_json(records_path)

        comparison_categories = brief.amenity_priorities or ["program_strategy", "public_interface", "amenity_strategy", "market_positioning"]
        comparison_rows: list[dict[str, str]] = []
        for record in records:
            for category in comparison_categories:
                comparison_rows.append(
                    {
                        "comp_id": record["comp_id"],
                        "project_name": record["project_name"],
                        "comparison_category": category,
                        "value": "research_target" if category in record.get("amenities", []) else "unknown",
                        "source_basis": "comparison priority; public source verification pending",
                        "confidence": "low",
                    }
                )

        scale_rows = [
            {
                "project_name": record["project_name"],
                "total_sf": record.get("total_sf") or "",
                "program_type": record["program_type"],
                "status": record["status"],
                "confidence": record["confidence"],
            }
            for record in records
        ]
        card_rows = [
            {
                "comp_id": record["comp_id"],
                "title": record["project_name"],
                "subtitle": f"{record['program_type']} | {record['location']}",
                "key_stats": {
                    "SF": record.get("total_sf") or "Needs research",
                    "Status": record["status"],
                    "Source count": record["source_count"],
                },
                "takeaway": record["relevance_summary"],
                "review_notes": record["review_notes"],
            }
            for record in records
        ]
        return {
            "comp_records_csv": write_csv(
                workspace.data / "comp_records.csv",
                records,
                [
                    "comp_id",
                    "project_name",
                    "location",
                    "program_type",
                    "total_sf",
                    "status",
                    "completion_year",
                    "developer_owner",
                    "architect_designer",
                    "relevance_summary",
                    "confidence",
                    "source_count",
                ],
            ),
            "comparison_matrix_csv": write_csv(
                workspace.data / "comparison_matrix.csv",
                comparison_rows,
                ["comp_id", "project_name", "comparison_category", "value", "source_basis", "confidence"],
            ),
            "amenity_matrix_csv": workspace.data / "comparison_matrix.csv",
            "scale_comparison_csv": write_csv(
                workspace.data / "scale_comparison.csv",
                scale_rows,
                ["project_name", "total_sf", "program_type", "status", "confidence"],
            ),
            "presentation_cards": write_json(workspace.data / "presentation_cards.json", card_rows),
        }

    def audit(self, brief: ProjectBrief) -> dict[str, Path]:
        workspace = self.workspace_for(brief)
        records_path = workspace.data / "comp_records.json"
        if not records_path.exists():
            self.research(brief)
        records = _load_json(records_path)
        flags: list[ReviewFlag] = []
        tasks: list[RevisionTask] = []

        for record in records:
            comp_id = record["comp_id"]
            if not record.get("total_sf"):
                flags.append(
                    ReviewFlag(
                        flag_id=slugify(f"{comp_id}-total-sf"),
                        comp_id=comp_id,
                        field="total_sf",
                        severity="high",
                        issue="Total SF is missing for this approved comp.",
                        recommendation="Run targeted public-source research for total SF before using in a deck.",
                    )
                )
            if int(record.get("source_count") or 0) == 0:
                flags.append(
                    ReviewFlag(
                        flag_id=slugify(f"{comp_id}-sources"),
                        comp_id=comp_id,
                        field="source_count",
                        severity="high",
                        issue="No verified public sources have been attached to this comp record.",
                        recommendation="Attach at least one primary or strong secondary source.",
                    )
                )
            if record.get("amenities"):
                flags.append(
                    ReviewFlag(
                        flag_id=slugify(f"{comp_id}-amenities"),
                        comp_id=comp_id,
                        field="amenities",
                        severity="medium",
                        issue="Amenities are research targets from the project brief, not verified comp offerings.",
                        recommendation="Verify each amenity against developer, architect, leasing, or planning sources.",
                    )
                )
            for flag in flags:
                if flag.comp_id == comp_id and flag.severity in {"high", "medium"}:
                    tasks.append(
                        RevisionTask(
                            task_id=slugify(f"revise-{flag.flag_id}"),
                            comp_id=flag.comp_id,
                            field=flag.field,
                            reason=flag.issue,
                        )
                    )

        return {
            "audit_report": write_json(workspace.data / "audit_report.json", flags),
            "review_flags_csv": write_csv(
                workspace.data / "review_flags.csv",
                [asdict(item) for item in flags],
                ["flag_id", "comp_id", "field", "severity", "issue", "recommendation", "status", "source_url", "created_at"],
            ),
            "revision_tasks": write_json(workspace.data / "revision_tasks.json", tasks),
        }

    def generate_outputs(self, brief: ProjectBrief) -> dict[str, Path]:
        workspace = self.workspace_for(brief)
        candidate_path = workspace.data / "candidate_comps.json"
        source_log_path = workspace.data / "source_log.json"
        approved_path = workspace.data / "approved_comps.json"
        if not candidate_path.exists() or not approved_path.exists():
            self.approve(brief, limit=1)
        repaired_path = workspace.data / "repaired_comps.json"
        candidate_source_path = repaired_path if repaired_path.exists() else candidate_path
        candidates = [_candidate_from_dict(item) for item in _load_json(candidate_source_path)]
        approved_ids = {str(item["comp_id"]) for item in _load_json(approved_path)}
        approved_candidates = [item for item in candidates if item.comp_id in approved_ids] or candidates[:1]
        criteria = self.agent.build_criteria(brief)
        metrics = self.agent.summarize_metrics(brief, approved_candidates)
        records_path = workspace.data / "comp_records.json"
        records = _load_json(records_path) if records_path.exists() else []
        if source_log_path.exists():
            source_log = [_source_log_from_dict(item) for item in _load_json(source_log_path)]
        else:
            source_log = self.agent.build_source_log(self.agent.build_source_queries(brief))

        readiness_path = create_comp_readiness_chart(approved_candidates, workspace.graphics / "approved_comp_readiness.svg")
        coverage_path = create_source_coverage_chart(source_log, workspace.graphics / "source_coverage.svg")
        metric_chart_path = create_metric_snapshot(metrics, workspace.graphics / "metric_snapshot.svg")
        images_dir = workspace.outputs / "images"
        json_dir = workspace.outputs / "json"
        images_dir.mkdir(parents=True, exist_ok=True)
        json_dir.mkdir(parents=True, exist_ok=True)
        deck_data = build_comp_study_deck_data(brief, records, approved_candidates, source_log)
        image_manifest_path = download_hero_images(deck_data, images_dir)
        image_manifest = _load_json(image_manifest_path) if image_manifest_path.exists() else []
        audit_items, field_tasks = _audit_deck_data(deck_data, image_manifest)
        field_results: list[dict[str, Any]] = []
        field_repair_limit = _field_repair_limit()
        search_provider = self.agent.search_provider
        if search_provider and field_tasks:
            for task in field_tasks[:field_repair_limit]:
                comp = next((item for item in deck_data.get("comps", []) if item.get("project_name") == task.get("project_name")), {})
                try:
                    result = search_provider.repair_field(comp, task)
                except Exception as error:
                    result = {"field": task.get("field"), "value": None, "warnings": [str(error)], "sources": []}
                result["task"] = task
                field_results.append(result)
                _apply_field_repair(deck_data, task, result)
            if any(_field_repair_has_image_candidates(result) for result in field_results):
                image_manifest_path = download_hero_images(deck_data, images_dir)
                image_manifest = _load_json(image_manifest_path) if image_manifest_path.exists() else image_manifest
                audit_items, field_tasks = _audit_deck_data(deck_data, image_manifest)
        image_manifest_json_path = write_json(json_dir / "image_manifest.json", image_manifest)
        write_json(json_dir / "deck_audit.json", audit_items)
        write_json(json_dir / "field_repair_tasks.json", field_tasks)
        write_json(json_dir / "field_repair_results.json", field_results)
        deck_path = create_concept_deck_from_data(
            deck_data,
            workspace.outputs / "comp_study_deck.pptx",
            data_output_dir=workspace.outputs,
        )
        manifest = {
            "approved_comp_readiness_chart": str(readiness_path),
            "source_coverage_chart": str(coverage_path),
            "metric_snapshot_chart": str(metric_chart_path),
            "poc_deck": str(deck_path),
            "comp_study_deck": str(deck_path),
            "deck_data": str(json_dir / "deck_data.json"),
            "deck_strategy": str(json_dir / "deck_strategy.json"),
            "approved_comps_normalized": str(json_dir / "approved_comps_normalized.json"),
            "source_metadata": str(json_dir / "source_metadata.json"),
            "diligence_notes": str(json_dir / "diligence_notes.json"),
            "deck_audit": str(json_dir / "deck_audit.json"),
            "field_repair_tasks": str(json_dir / "field_repair_tasks.json"),
            "field_repair_results": str(json_dir / "field_repair_results.json"),
            "image_manifest": str(image_manifest_json_path),
            "images": str(images_dir),
            "json": str(json_dir),
        }
        return {
            "approved_comp_readiness_chart": readiness_path,
            "source_coverage_chart": coverage_path,
            "metric_snapshot_chart": metric_chart_path,
            "poc_deck": deck_path,
            "comp_study_deck": deck_path,
            "deck_data": json_dir / "deck_data.json",
            "deck_strategy": json_dir / "deck_strategy.json",
            "approved_comps_normalized": json_dir / "approved_comps_normalized.json",
            "source_metadata": json_dir / "source_metadata.json",
            "diligence_notes": json_dir / "diligence_notes.json",
            "deck_audit": json_dir / "deck_audit.json",
            "field_repair_tasks": json_dir / "field_repair_tasks.json",
            "field_repair_results": json_dir / "field_repair_results.json",
            "image_manifest": image_manifest_json_path,
            "output_manifest": write_json(workspace.data / "output_manifest.json", manifest),
        }

    def run_poc(self, brief: ProjectBrief, source_selection: SourceSelection | None = None) -> dict[str, str]:
        paths: dict[str, Path] = {}
        for stage_paths in (
            self.discover(brief, source_selection),
            self.approve(brief, limit=1, notes="Auto-approved candidate set for POC run."),
            self.research(brief),
            self.format_outputs(brief),
            self.generate_outputs(brief),
            self.audit(brief),
        ):
            paths.update(stage_paths)
        manifest = {key: str(path) for key, path in paths.items()}
        workspace = self.workspace_for(brief)
        write_json(workspace.data / "poc_manifest.json", manifest)
        return manifest


def _merge_candidate_evidence(base: CompCandidate, update: CompCandidate) -> CompCandidate:
    attrs = _merge_dict_fill_blanks(base.known_attributes, update.known_attributes)
    sources = _merge_sources(base.known_attributes.get("_sources"), update.known_attributes.get("_sources"))
    if sources:
        attrs["_sources"] = sources
    missing = [value for value in update.missing_attributes if value] or [value for value in base.missing_attributes if value]
    notes = [*base.source_notes]
    for note in update.source_notes:
        if note and note not in notes:
            notes.append(note)
    return CompCandidate(
        comp_id=base.comp_id,
        comp_name=update.comp_name if update.comp_name and update.comp_name.lower().startswith("live search candidate") is False else base.comp_name,
        location=update.location or base.location,
        comp_type=update.comp_type or base.comp_type,
        relevance_score=max(base.relevance_score, update.relevance_score),
        status=update.status or base.status,
        known_attributes=attrs,
        missing_attributes=missing,
        source_notes=notes,
    )


def _merge_dict_fill_blanks(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (update or {}).items():
        if _is_blank(value):
            continue
        current = merged.get(key)
        if _is_blank(current) or _is_placeholder_value(current) or key in {"hero_image", "image_package", "image_candidates"}:
            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = _merge_dict_fill_blanks(current, value)
            elif isinstance(current, list) and isinstance(value, list):
                merged[key] = _merge_lists(current, value)
            else:
                merged[key] = value
    return merged


def _merge_sources(left: Any, right: Any) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in (left, right):
        if not isinstance(group, list):
            continue
        for source in group:
            if not isinstance(source, dict):
                continue
            key = str(source.get("url") or source.get("name") or source.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            sources.append(source)
    return sources


def _merge_lists(left: list[Any], right: list[Any]) -> list[Any]:
    merged = list(left)
    seen = {json.dumps(item, sort_keys=True, default=str) for item in merged}
    for item in right:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def _candidate_snapshot(candidate: CompCandidate, *, stage: str, warnings: list[str] | None = None, missing_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "stage": stage,
        "comp_id": candidate.comp_id,
        "comp_name": candidate.comp_name,
        "location": candidate.location,
        "comp_type": candidate.comp_type,
        "relevance_score": candidate.relevance_score,
        "status": candidate.status,
        "known_attributes": candidate.known_attributes,
        "adaptive_field_labels": _suggested_adaptive_labels_from_candidate(candidate),
        "missing_attributes": candidate.missing_attributes,
        "source_notes": candidate.source_notes,
        "missing_fields": missing_fields if missing_fields is not None else _candidate_missing_fields(candidate),
        "warnings": warnings or [],
    }


def _candidate_missing_fields(candidate: CompCandidate) -> list[str]:
    attrs = candidate.known_attributes or {}
    fields: list[str] = []
    if _is_blank(attrs.get("total_sf")) and _is_blank(attrs.get("gross_area")) and _is_blank(attrs.get("rentable_area")):
        fields.append("scale")
    if _is_blank(attrs.get("completion_year")) and _is_blank(attrs.get("status_year")):
        fields.append("year_status")
    if _is_blank(attrs.get("developer_owner")):
        fields.append("owner_developer")
    if _is_blank(attrs.get("architect_designer")):
        fields.append("architect_designer")
    if _is_blank(attrs.get("intervention_type")) and _is_blank(attrs.get("intervention_strategy")):
        fields.append("intervention_type")
    if _is_blank(attrs.get("key_program")) and _is_blank(attrs.get("program_type")):
        fields.append("key_program")
    if _is_blank(attrs.get("defining_move")) and (_is_blank(attrs.get("presentation_takeaway")) or _is_placeholder_value(attrs.get("presentation_takeaway"))):
        fields.append("defining_move")
    if _is_blank(attrs.get("relevance_to_subject")) and (_is_blank(attrs.get("presentation_takeaway")) or _is_placeholder_value(attrs.get("presentation_takeaway"))):
        fields.append("relevance_statement")
    if len(attrs.get("_sources") if isinstance(attrs.get("_sources"), list) else []) < 3:
        fields.append("primary_sources")
    if _image_candidate_count(attrs) < 3:
        fields.append("image_candidate_coverage")
    adaptive = attrs.get("adaptive_fields") if isinstance(attrs.get("adaptive_fields"), dict) else {}
    if len([value for value in adaptive.values() if not _is_blank(value)]) < 3:
        fields.append("adaptive_fields")
    return fields


def _suggested_adaptive_labels_from_candidate(candidate: CompCandidate) -> list[str]:
    text = " ".join([candidate.comp_name, candidate.comp_type, str(candidate.known_attributes), " ".join(candidate.source_notes)]).lower()
    if any(token in text for token in ("lobby", "reposition", "podium", "base")):
        return ["Podium / Base Strategy", "Arrival / Lobby Move", "Amenity / User Experience", "Street / Public Interface", "Positioning Intent"]
    if any(token in text for token in ("house", "single family", "villa")):
        return ["Site Relationship", "Material Strategy", "Indoor / Outdoor Living", "Plan Organization", "Privacy / Views"]
    if any(token in text for token in ("civic", "landmark", "museum", "library", "cultural")):
        return ["Civic Role", "Public Access", "Symbolic Identity", "Preservation / Expansion Move", "Gathering Spaces"]
    if any(token in text for token in ("new construction", "tower", "high-rise", "high rise")):
        return ["Massing / Identity", "Arrival Sequence", "Amenity Stack", "Site Integration", "Market Positioning"]
    if any(token in text for token in ("adaptive reuse", "conversion", "reuse")):
        return ["Original Use", "New Use", "Preservation / Structure Move", "Program Transformation", "Sustainability Claim"]
    return ["Program Strategy", "Public Interface", "User Experience", "Design Strategy", "Market Positioning"]


def _image_candidate_count(attrs: dict[str, Any]) -> int:
    urls: set[str] = set()
    hero = attrs.get("hero_image") if isinstance(attrs.get("hero_image"), dict) else {}
    package = attrs.get("image_package") if isinstance(attrs.get("image_package"), dict) else {}
    for value in [hero.get("url"), *(hero.get("fallback_urls") or [])]:
        if value:
            urls.add(str(value))
    for slot in ("overall", "focus", "detail"):
        slot_data = package.get(slot) if isinstance(package.get(slot), dict) else {}
        if slot_data.get("url"):
            urls.add(str(slot_data["url"]))
    for candidate in attrs.get("image_candidates") or []:
        if isinstance(candidate, dict) and candidate.get("url"):
            urls.add(str(candidate["url"]))
    return len(urls)


def _is_blank(value: Any) -> bool:
    return value in (None, "", [], {}, "—", "None", "not_available")


def _is_placeholder_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized.startswith("user-added comparable project") or normalized.startswith("approved precedent for")


def _field_repair_limit() -> int:
    try:
        return max(0, int(os.getenv("COMP_AGENT_FIELD_REPAIR_LIMIT", "4")))
    except ValueError:
        return 4


def _research_concurrency() -> int:
    try:
        return max(1, int(os.getenv("COMP_AGENT_RESEARCH_CONCURRENCY", str(DEFAULT_RESEARCH_CONCURRENCY))))
    except ValueError:
        return DEFAULT_RESEARCH_CONCURRENCY


def _run_parallel_research(
    approved_items: list[dict[str, Any]],
    worker,
    concurrency: int,
) -> list[dict[str, Any]]:
    if concurrency <= 1 or len(approved_items) <= 1:
        return [worker(index, item) for index, item in enumerate(approved_items)]

    max_workers = min(concurrency, len(approved_items))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, index, item) for index, item in enumerate(approved_items)]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: int(item["index"]))


def _safe_source_log(result: Any) -> list[Any]:
    return list(getattr(result, "source_log", []) or [])


def _audit_deck_data(deck_data: dict[str, Any], image_manifest: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_by_name = {str(item.get("project_name") or ""): item for item in image_manifest if isinstance(item, dict)}
    audit_items: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    priority = 1
    for comp in deck_data.get("comps", []) or []:
        if not isinstance(comp, dict):
            continue
        project_name = str(comp.get("project_name") or "")
        manifest = manifest_by_name.get(project_name, {})
        saved_images = sum(1 for slot in (manifest.get("slots") or {}).values() if isinstance(slot, dict) and slot.get("status") == "saved")
        checks = [
            ("images", saved_images >= 3, f"{3 - saved_images} real image slot(s) missing" if saved_images < 3 else ""),
            ("status_year", not _is_blank(comp.get("status_year")), "Year / Status is missing"),
            ("scale", not _is_blank((comp.get("scale") or {}).get("display")), "Scale is missing"),
            ("owner_developer", not _is_blank(comp.get("owner_developer")), "Owner / Developer is missing"),
            ("architect_designer", not _is_blank(comp.get("architect_designer")), "Architect / Designer is missing"),
            ("relevance_to_subject", not _is_blank(comp.get("relevance_to_subject")), "Relevance statement is missing"),
            ("defining_move", not _is_blank(comp.get("defining_move")), "Defining move is missing"),
            ("primary_sources", len(comp.get("primary_sources") or []) >= 3, "Primary source coverage is low"),
        ]
        for field, passed, issue in checks:
            audit_items.append(
                {
                    "project_name": project_name,
                    "field": field,
                    "status": "pass" if passed else "fail",
                    "issue": issue,
                    "priority": priority if not passed else None,
                }
            )
            if not passed:
                tasks.append(
                    {
                        "task_id": slugify(f"field-repair-{project_name}-{field}"),
                        "project_name": project_name,
                        "field": field,
                        "priority": priority,
                        "issue": issue,
                        "query_intent": _field_repair_intent(comp, field, issue),
                    }
                )
                priority += 1
    return audit_items, sorted(tasks, key=lambda item: int(item["priority"]))


def _field_repair_intent(comp: dict[str, Any], field: str, issue: str) -> str:
    name = comp.get("project_name") or "the comparable project"
    location = comp.get("location") or ""
    if field == "images":
        return f"Find real direct image URLs for {name} {location}, prioritizing missing overall/focus/detail presentation images."
    if field == "status_year":
        return f"Find the completion, opening, delivery, or current development status year for {name} {location}."
    if field == "scale":
        return f"Find the gross area, rentable area, unit count, keys, floors, or other client-facing scale metric for {name} {location}."
    if field == "owner_developer":
        return f"Find the owner or developer for {name} {location}."
    if field == "architect_designer":
        return f"Find the architect, designer, or design firm for {name} {location}."
    if field in {"relevance_to_subject", "defining_move"}:
        return f"Find sourced project description facts that explain the key design/intervention move for {name} {location}."
    return f"Find authoritative source coverage for {name} {location}: {issue}."


def _apply_field_repair(deck_data: dict[str, Any], task: dict[str, Any], result: dict[str, Any]) -> None:
    if not result or result.get("confidence") == "unresolved":
        return
    comp = next((item for item in deck_data.get("comps", []) if item.get("project_name") == task.get("project_name")), None)
    if not comp:
        return
    field = str(task.get("field") or "")
    value = result.get("display_value") or result.get("value")
    if field == "images":
        candidates = result.get("image_candidates") if isinstance(result.get("image_candidates"), list) else []
        if candidates:
            existing = comp.get("image_candidates") if isinstance(comp.get("image_candidates"), list) else []
            comp["image_candidates"] = _merge_lists(existing, candidates)
        return
    if _is_blank(value):
        return
    if field == "scale":
        scale = comp.get("scale") if isinstance(comp.get("scale"), dict) else {}
        if _is_blank(scale.get("display")):
            scale["display"] = str(value)
            comp["scale"] = scale
    elif field in {"status_year", "owner_developer", "architect_designer", "relevance_to_subject", "defining_move"}:
        if _is_blank(comp.get(field)):
            comp[field] = str(value)
    elif field == "primary_sources":
        sources = result.get("sources") if isinstance(result.get("sources"), list) else []
        if sources and not comp.get("primary_sources"):
            comp["primary_sources"] = sources[:3]
    sources = result.get("sources") if isinstance(result.get("sources"), list) else []
    if sources:
        comp["primary_sources"] = _merge_sources(comp.get("primary_sources"), sources)[:3]


def _field_repair_has_image_candidates(result: dict[str, Any]) -> bool:
    return bool(result.get("task", {}).get("field") == "images" and isinstance(result.get("image_candidates"), list) and result.get("image_candidates"))
