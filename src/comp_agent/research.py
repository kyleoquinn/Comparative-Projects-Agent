from __future__ import annotations

from comp_agent.models import (
    CompCandidate,
    CompCriterion,
    MetricSummary,
    ProjectBrief,
    SourceLogEntry,
    SourceQuery,
)
from comp_agent.openai_search import OpenAIWebSearchProvider
from comp_agent.workspace import slugify


DEFAULT_COMP_TYPES = [
    "same program",
    "similar scale",
    "design precedent",
    "market positioning",
]


class CompResearchAgent:
    """Builds a consistent concept-stage comps research package.

    This class currently creates a structured research plan and presentation
    output skeleton. Live source adapters can be added behind this contract.
    """

    def __init__(self, search_provider: OpenAIWebSearchProvider | None = None) -> None:
        self.search_provider = search_provider or OpenAIWebSearchProvider.from_env()
        self._live_search_cache: dict[str, object] = {}

    def build_criteria(self, brief: ProjectBrief) -> list[CompCriterion]:
        comp_types = brief.comp_types or DEFAULT_COMP_TYPES
        criteria = [
            CompCriterion(
                criterion="Program alignment",
                reason=f"Find precedents with comparable {brief.program_type} drivers.",
                priority="high",
                source_hint="brokerage reports, project pages, planning submissions",
            ),
            CompCriterion(
                criterion="Geographic relevance",
                reason=f"Prioritize comps in or comparable to {brief.geography}.",
                priority="high",
                source_hint="local market reports, GIS, municipal records",
            ),
            CompCriterion(
                criterion="Presentation defensibility",
                reason="Favor comps with clear public sources and reusable facts.",
                priority="high",
                source_hint="official project pages, permit records, press releases",
            ),
        ]
        for comp_type in comp_types:
            criteria.append(
                CompCriterion(
                    criterion=f"{comp_type.title()} lens",
                    reason=f"Include comps that explain the project's {comp_type} opportunity.",
                    priority="medium",
                    source_hint="targeted web search and internal precedent library",
                )
            )
        for key, value in brief.filters.items():
            criteria.append(
                CompCriterion(
                    criterion=str(key).replace("_", " ").title(),
                    reason=f"Filter target comps where {key} is {value}.",
                    priority="medium",
                    source_hint="candidate screening table",
                )
            )
        return criteria

    def build_source_queries(self, brief: ProjectBrief) -> list[SourceQuery]:
        comp_types = brief.comp_types or DEFAULT_COMP_TYPES
        base_terms = [brief.address, brief.geography, brief.program_type]
        queries = [
            SourceQuery(
                topic="nearby precedent projects",
                query=f'"{brief.geography}" "{brief.program_type}" development precedent case study',
                target_source_type="project pages and design press",
                why_it_matters="Identifies projects with similar positioning and public narrative.",
            ),
            SourceQuery(
                topic="market benchmark reports",
                query=f'"{brief.geography}" "{brief.program_type}" market report rent sales comps',
                target_source_type="brokerage and market reports",
                why_it_matters="Supports concept-level assumptions with third-party market context.",
            ),
            SourceQuery(
                topic="public approvals and permits",
                query=f'"{brief.address}" planning permit entitlement development',
                target_source_type="municipal planning and permit records",
                why_it_matters="Documents official project constraints and entitlement signals.",
            ),
        ]
        for comp_type in comp_types:
            queries.append(
                SourceQuery(
                    topic=f"{comp_type} comps",
                    query=" ".join([f'"{term}"' for term in base_terms if term and term != "unknown"])
                    + f' "{comp_type}" comparable project',
                    target_source_type="web search / internal precedent library",
                    why_it_matters=f"Finds candidates for the {comp_type} presentation lane.",
                )
            )
        return queries

    def identify_candidates(self, brief: ProjectBrief) -> list[CompCandidate]:
        live_result = self._live_search(brief)
        if live_result and live_result.candidates:
            return live_result.candidates
        if live_result and live_result.warnings:
            return []

        candidates: list[CompCandidate] = []
        for index, comp_type in enumerate(brief.comp_types or DEFAULT_COMP_TYPES, start=1):
            candidates.append(
                CompCandidate(
                    comp_id=slugify(f"{brief.project_name}-{comp_type}-{index}"),
                    comp_name=f"Research target {index}: {comp_type.title()} comp",
                    location=brief.geography,
                    comp_type=comp_type,
                    relevance_score=max(55, 90 - index * 7),
                    status="needs_research",
                    known_attributes={
                        "program_type": brief.program_type,
                        "target_radius_miles": brief.radius_miles,
                        "time_horizon_years": brief.time_horizon_years,
                    },
                    missing_attributes=[
                        "project_name",
                        "address",
                        "delivery_year",
                        "size",
                        "cost_or_value",
                        "source_url",
                        "presentation_takeaway",
                    ],
                    source_notes=[
                        "Candidate placeholder generated from comp strategy.",
                        "Replace with researched project after source review.",
                    ],
                )
            )
        return candidates

    def summarize_metrics(self, brief: ProjectBrief, candidates: list[CompCandidate]) -> list[MetricSummary]:
        return [
            MetricSummary(
                metric="Comp lanes",
                value=str(len(candidates)),
                confidence="high",
                use_in_presentation="Shows how many precedent lenses are being tracked.",
                source_basis="Generated from selected comp types.",
            ),
            MetricSummary(
                metric="Research radius",
                value=f"{brief.radius_miles:g} miles",
                confidence="high",
                use_in_presentation="Defines the first-pass search geography.",
                source_basis="Project brief input.",
            ),
            MetricSummary(
                metric="Evidence readiness",
                value="Source plan created; factual comp rows pending research.",
                confidence="medium",
                use_in_presentation="Use as an internal QA indicator before client-facing use.",
                source_basis="Candidate status and source log.",
            ),
            MetricSummary(
                metric="Presentation priority",
                value=brief.presentation_priorities[0] if brief.presentation_priorities else "defensible concept narrative",
                confidence="medium",
                use_in_presentation="Anchors chart and table selection.",
                source_basis="Project brief input.",
            ),
        ]

    def build_source_log(self, queries: list[SourceQuery]) -> list[SourceLogEntry]:
        if self._live_search_cache:
            live_result = next(iter(self._live_search_cache.values()))
            if getattr(live_result, "source_log", None):
                return list(live_result.source_log)
            if getattr(live_result, "warnings", None):
                return [
                    SourceLogEntry(
                        source_name="OpenAI live search",
                        source_type="openai web_search",
                        url_or_search="Responses API web_search",
                        related_output="openai_live_search",
                        status="failed",
                        notes="; ".join(live_result.warnings),
                    )
                ]
        return [
            SourceLogEntry(
                source_name=query.topic.title(),
                source_type=query.target_source_type,
                url_or_search=query.query,
                related_output="source_query_plan.csv",
                notes=query.why_it_matters,
            )
            for query in queries
        ]

    def _live_search(self, brief: ProjectBrief):
        if not self.search_provider:
            return None
        cache_key = f"{brief.project_name}|{brief.address}|{brief.program_type}|{brief.geography}|{brief.comp_types}"
        if cache_key not in self._live_search_cache:
            max_candidates = _max_candidates_for(brief)
            self._live_search_cache[cache_key] = self.search_provider.discover(brief, max_candidates=max_candidates)
        return self._live_search_cache[cache_key]


def _max_candidates_for(brief: ProjectBrief) -> int:
    raw_value = brief.filters.get("max_comps") or brief.filters.get("max_candidates")
    if raw_value in (None, ""):
        raw_value = __import__("os").getenv("COMP_AGENT_MAX_CANDIDATES", "5")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 5
    return max(1, min(12, value))
