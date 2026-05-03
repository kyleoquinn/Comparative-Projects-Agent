from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ProjectBrief:
    project_name: str
    address: str
    program_type: str
    total_sf: int | None = None
    amenity_priorities: list[str] = field(default_factory=list)
    design_priorities: list[str] = field(default_factory=list)
    geography: str = "unknown"
    scope_summary: str = ""
    comp_types: list[str] = field(default_factory=list)
    comp_guidance: str = ""
    must_include_comps: list[dict[str, str]] = field(default_factory=list)
    radius_miles: float = 5.0
    time_horizon_years: int = 10
    audience: str = "concept presentation"
    filters: dict[str, Any] = field(default_factory=dict)
    presentation_priorities: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProjectBrief":
        comp_inputs = _comparative_project_inputs(payload)
        comp_types = comp_inputs.get("comp_types") or payload.get("comp_types") or payload.get("comp_type") or []
        priorities = payload.get("presentation_priorities") or []
        if isinstance(priorities, str):
            priorities = [priorities]
        amenity_priorities = _as_string_list(payload.get("amenity_priorities") or payload.get("design_priorities") or [])
        design_priorities = _as_string_list(payload.get("design_priorities") or amenity_priorities)
        return cls(
            project_name=str(payload["project_name"]),
            address=str(payload["address"]),
            program_type=str(payload["program_type"]),
            total_sf=int(payload["total_sf"]) if payload.get("total_sf") not in (None, "") else None,
            amenity_priorities=amenity_priorities,
            design_priorities=design_priorities,
            geography=str(payload.get("geography") or payload.get("market") or "unknown"),
            scope_summary=str(payload.get("scope_summary") or ""),
            comp_types=_as_string_list(comp_types),
            comp_guidance=str(comp_inputs.get("comp_guidance") or payload.get("comp_guidance") or ""),
            must_include_comps=_as_comp_list(comp_inputs.get("must_include_comps") or payload.get("must_include_comps") or []),
            radius_miles=float(payload.get("radius_miles", 5.0)),
            time_horizon_years=int(payload.get("time_horizon_years", 10)),
            audience=str(payload.get("audience", "concept presentation")),
            filters=dict(payload.get("filters") or {}),
            presentation_priorities=[str(item) for item in priorities],
        )


def _comparative_project_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    agent_inputs = payload.get("agent_inputs") if isinstance(payload.get("agent_inputs"), dict) else {}
    nested = agent_inputs.get("comparative_projects") if isinstance(agent_inputs.get("comparative_projects"), dict) else {}
    direct = payload.get("comparative_projects") if isinstance(payload.get("comparative_projects"), dict) else {}
    return {**direct, **nested}


def _as_string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item) for item in value if str(item).strip()]


def _as_comp_list(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        rows = []
        for line in value.splitlines():
            parts = [part.strip() for part in line.split("|")]
            if parts and parts[0]:
                rows.append({"name": parts[0], "location": parts[1] if len(parts) > 1 else "", "note": parts[2] if len(parts) > 2 else ""})
        return rows
    comps = []
    for item in value:
        if isinstance(item, str):
            parsed = _as_comp_list(item)
            comps.extend(parsed)
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("comp_name") or item.get("project_name") or "").strip()
        if not name:
            continue
        comps.append(
            {
                "name": name,
                "location": str(item.get("location") or "").strip(),
                "note": str(item.get("note") or item.get("reason") or item.get("guidance") or "").strip(),
            }
        )
    return comps


@dataclass(slots=True)
class CompCriterion:
    criterion: str
    reason: str
    priority: str = "medium"
    source_hint: str = ""


@dataclass(slots=True)
class SourceQuery:
    topic: str
    query: str
    target_source_type: str
    why_it_matters: str


@dataclass(slots=True)
class CompCandidate:
    comp_id: str
    comp_name: str
    location: str
    comp_type: str
    relevance_score: int
    status: str
    known_attributes: dict[str, Any] = field(default_factory=dict)
    missing_attributes: list[str] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MetricSummary:
    metric: str
    value: str
    confidence: str
    use_in_presentation: str
    source_basis: str


@dataclass(slots=True)
class SourceLogEntry:
    source_name: str
    source_type: str
    url_or_search: str
    related_output: str
    status: str = "planned"
    retrieved_at: str = ""
    notes: str = ""


@dataclass(slots=True)
class SourceSelection:
    public_web: bool = True
    developer_owner_sites: bool = True
    architect_design_sites: bool = True
    brokerage_market_reports: bool = True
    planning_permit_portals: bool = True
    news_press: bool = True
    design_publications_awards: bool = True
    uploaded_user_files: bool = False
    internal_database: bool = False

    @classmethod
    def public_only(cls) -> "SourceSelection":
        return cls(internal_database=False)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SourceSelection":
        if not payload:
            return cls.public_only()
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: bool(value) for key, value in payload.items() if key in allowed})

    def enabled_labels(self) -> list[str]:
        labels = {
            "public_web": "public web",
            "developer_owner_sites": "developer / owner websites",
            "architect_design_sites": "architect / design firm websites",
            "brokerage_market_reports": "brokerage / market reports",
            "planning_permit_portals": "planning / permit portals",
            "news_press": "news / press releases",
            "design_publications_awards": "design publications / awards",
            "uploaded_user_files": "uploaded user files",
            "internal_database": "internal proprietary database",
        }
        return [label for key, label in labels.items() if getattr(self, key)]


@dataclass(slots=True)
class ApprovalDecision:
    comp_id: str
    decision: str = "approved"
    notes: str = ""
    decided_by: str = "user"
    decided_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class ApprovedComp:
    comp_id: str
    comp_name: str
    location: str
    comp_type: str
    approval_notes: str = ""


@dataclass(slots=True)
class ExtractedFact:
    comp_id: str
    field: str
    value: Any
    unit: str = ""
    source_provider: str = "public_web"
    source_type: str = ""
    source_url: str = ""
    source_name: str = ""
    retrieved_at: str = field(default_factory=utc_now_iso)
    confidence: str = "unknown"
    access_level: str = "public"
    can_include_in_deck: bool = True
    human_review_required: bool = False
    notes: str = ""


@dataclass(slots=True)
class CompRecord:
    comp_id: str
    project_name: str
    location: str
    program_type: str
    total_sf: int | None = None
    status: str = "needs_research"
    completion_year: str = ""
    developer_owner: str = ""
    architect_designer: str = ""
    amenities: list[str] = field(default_factory=list)
    relevance_summary: str = ""
    confidence: str = "low"
    source_count: int = 0
    review_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AmenityRecord:
    comp_id: str
    project_name: str
    amenity_category: str
    present: str = "unknown"
    source_basis: str = "not yet verified"
    confidence: str = "unknown"


@dataclass(slots=True)
class ReviewFlag:
    flag_id: str
    comp_id: str
    field: str
    severity: str
    issue: str
    recommendation: str
    status: str = "open"
    source_url: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class RevisionTask:
    task_id: str
    comp_id: str
    field: str
    reason: str
    requested_action: str = "verify_or_replace"
    status: str = "queued"
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class UserDecision:
    decision_id: str
    target_type: str
    target_id: str
    decision: str
    notes: str = ""
    decided_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class PackageManifest:
    project_name: str
    address: str
    generated_at: str
    research_status: str
    files: dict[str, str]
