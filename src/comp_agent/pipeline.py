from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from comp_agent.charts import create_comp_readiness_chart, create_metric_snapshot, create_source_coverage_chart
from comp_agent.deck import create_concept_deck
from comp_agent.models import PackageManifest, ProjectBrief, utc_now_iso
from comp_agent.research import CompResearchAgent
from comp_agent.workspace import ProjectWorkspace, write_csv, write_json


class CompPackagePipeline:
    def __init__(self, output_root: str | Path = "projects") -> None:
        self.output_root = Path(output_root)
        self.agent = CompResearchAgent()

    def run(self, brief: ProjectBrief) -> PackageManifest:
        workspace = ProjectWorkspace(self.output_root, brief.project_name).create()

        criteria = self.agent.build_criteria(brief)
        queries = self.agent.build_source_queries(brief)
        candidates = self.agent.identify_candidates(brief)
        metrics = self.agent.summarize_metrics(brief, candidates)
        source_log = self.agent.build_source_log(queries)

        input_path = write_json(workspace.inputs / "project_brief.json", brief)
        criteria_path = write_json(workspace.data / "comp_criteria.json", criteria)
        query_path = write_csv(
            workspace.data / "source_query_plan.csv",
            [asdict(item) for item in queries],
            ["topic", "query", "target_source_type", "why_it_matters"],
        )
        candidate_path = write_csv(
            workspace.data / "comp_candidates.csv",
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
                "comp_name",
                "location",
                "comp_type",
                "relevance_score",
                "status",
                "known_attributes",
                "missing_attributes",
                "source_notes",
            ],
        )
        metrics_path = write_json(workspace.data / "metrics_summary.json", metrics)
        source_log_path = write_json(workspace.data / "source_log.json", source_log)

        readiness_path = create_comp_readiness_chart(candidates, workspace.graphics / "comp_readiness.svg")
        coverage_path = create_source_coverage_chart(source_log, workspace.graphics / "source_coverage.svg")
        metric_chart_path = create_metric_snapshot(metrics, workspace.graphics / "metric_snapshot.svg")
        deck_path = create_concept_deck(
            brief,
            criteria,
            candidates,
            metrics,
            source_log,
            workspace.outputs / "concept_comps_packet.pptx",
        )

        manifest = PackageManifest(
            project_name=brief.project_name,
            address=brief.address,
            generated_at=utc_now_iso(),
            research_status="structured_package_created_live_sources_pending",
            files={
                "project_brief": str(input_path),
                "comp_criteria": str(criteria_path),
                "source_query_plan": str(query_path),
                "comp_candidates": str(candidate_path),
                "metrics_summary": str(metrics_path),
                "source_log": str(source_log_path),
                "comp_readiness_chart": str(readiness_path),
                "source_coverage_chart": str(coverage_path),
                "metric_snapshot_chart": str(metric_chart_path),
                "concept_deck": str(deck_path),
            },
        )
        write_json(workspace.data / "package_manifest.json", manifest)
        return manifest
