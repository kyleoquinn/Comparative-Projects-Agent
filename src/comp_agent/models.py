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
    geography: str = "unknown"
    comp_types: list[str] = field(default_factory=list)
    radius_miles: float = 5.0
    time_horizon_years: int = 10
    audience: str = "concept presentation"
    filters: dict[str, Any] = field(default_factory=dict)
    presentation_priorities: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProjectBrief":
        comp_types = payload.get("comp_types") or payload.get("comp_type") or []
        if isinstance(comp_types, str):
            comp_types = [comp_types]
        priorities = payload.get("presentation_priorities") or []
        if isinstance(priorities, str):
            priorities = [priorities]
        return cls(
            project_name=str(payload["project_name"]),
            address=str(payload["address"]),
            program_type=str(payload["program_type"]),
            total_sf=int(payload["total_sf"]) if payload.get("total_sf") not in (None, "") else None,
            amenity_priorities=[str(item) for item in payload.get("amenity_priorities", [])],
            geography=str(payload.get("geography") or payload.get("market") or "unknown"),
            comp_types=[str(item) for item in comp_types],
            radius_miles=float(payload.get("radius_miles", 5.0)),
            time_horizon_years=int(payload.get("time_horizon_years", 10)),
            audience=str(payload.get("audience", "concept presentation")),
            filters=dict(payload.get("filters") or {}),
            presentation_priorities=[str(item) for item in priorities],
        )


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
