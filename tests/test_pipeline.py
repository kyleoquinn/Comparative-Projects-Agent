from __future__ import annotations

import csv
import json
from pathlib import Path

from comp_agent.models import CompCandidate, ProjectBrief, SourceLogEntry
from comp_agent.openai_search import LiveSearchResult, OpenAIWebSearchProvider
from comp_agent.pipeline import CompPackagePipeline
from comp_agent.stages import CompAppStages, _apply_field_repair, _audit_deck_data
from comp_agent.ui import _brief_from_payload, _merge_user_defined_candidates, _must_include_comps_for_discovery
from comp_agent.workspace import write_json
from comp_agent.deck_data import DEFAULT_SUMMARY_COLUMNS, FEATURE_MATRIX_MAX_COLUMNS, GENERIC_FEATURE_COLUMNS, build_comp_study_deck_data, build_deck_strategy, normalize_comp_for_deck
from comp_agent.deck import ACCENT, ACCENT_2, FONT, LINE, MUTED, create_concept_deck_from_data, _compact_feature_label, _design_lever_groups, _heat_color, _matrix_header_shared_font_size, _matrix_title, _profile_tags, _profile_title_text, _wrap_matrix_label


def test_pipeline_creates_expected_outputs(tmp_path):
    brief = ProjectBrief(
        project_name="Test Package",
        address="100 Main St, Example City",
        program_type="mixed-use",
        geography="Example City",
        comp_types=["mixed-use podium", "adaptive reuse"],
        presentation_priorities=["defensible source trail"],
    )

    manifest = CompPackagePipeline(output_root=tmp_path).run(brief)

    expected_keys = {
        "project_brief",
        "comp_criteria",
        "source_query_plan",
        "comp_candidates",
        "metrics_summary",
        "source_log",
        "comp_readiness_chart",
        "source_coverage_chart",
        "metric_snapshot_chart",
        "concept_deck",
    }
    assert expected_keys.issubset(manifest.files)
    for path in manifest.files.values():
        assert tmp_path in __import__("pathlib").Path(path).parents
        assert __import__("pathlib").Path(path).exists()

    with open(manifest.files["comp_candidates"], newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"needs_research"}


def test_deck_uses_pcp_graphic_standards_palette_and_font():
    assert FONT == "Segoe UI"
    assert (str(MUTED), str(LINE)) == ("A9A19B", "BEB6AF")
    assert (str(ACCENT), str(ACCENT_2)) == ("00A95C", "FFAC2A")
    assert str(_heat_color(0)) == "FFAC2A"
    assert str(_heat_color(1)) == "00A95C"


def test_project_brief_accepts_future_comp_agent_packet():
    brief = ProjectBrief.from_dict(
        {
            "project_name": "200 Vesey Repositioning",
            "address": "200 Vesey Street, New York, NY",
            "program_type": "office repositioning",
            "geography": "New York City",
            "scope_summary": "Lobby, tenant amenity, retail, and public realm upgrades.",
            "design_priorities": ["arrival experience", "tenant amenities"],
            "agent_inputs": {
                "comparative_projects": {
                    "comp_guidance": "Prioritize recent lobby repositioning precedents.",
                    "comp_types": ["office lobby repositioning", "tenant amenity upgrade"],
                    "must_include_comps": [
                        {"name": "660 Fifth Avenue", "location": "New York, NY", "note": "Lobby precedent"}
                    ],
                }
            },
        }
    )

    assert brief.scope_summary.startswith("Lobby")
    assert brief.design_priorities == ["arrival experience", "tenant amenities"]
    assert brief.amenity_priorities == ["arrival experience", "tenant amenities"]
    assert brief.comp_guidance == "Prioritize recent lobby repositioning precedents."
    assert brief.comp_types == ["office lobby repositioning", "tenant amenity upgrade"]
    assert brief.must_include_comps == [{"name": "660 Fifth Avenue", "location": "New York, NY", "note": "Lobby precedent"}]


def test_future_packet_must_include_comps_merge_with_poc_ui_entries():
    brief = _brief_from_payload(
        {
            "project_name": "200 Vesey Repositioning",
            "address": "200 Vesey Street, New York, NY",
            "program_type": "office repositioning",
            "geography": "New York City",
            "comparative_projects": {
                "must_include_comps": [
                    {"name": "660 Fifth Avenue", "location": "New York, NY", "note": "From packet"}
                ]
            },
        }
    )

    must_include = _must_include_comps_for_discovery(
        brief,
        "660 5th Ave | New York, NY | Duplicate from UI\n343 Madison | New York, NY | UI add",
    )
    candidates = _merge_user_defined_candidates(brief, [], must_include)

    assert [item["comp_name"] for item in candidates] == ["660 Fifth Avenue", "343 Madison"]
    assert candidates[0]["candidate_source"] == "user_defined"
    assert candidates[0]["known_attributes"]["presentation_takeaway"] == "From packet"


def test_staged_poc_creates_reviewable_single_comp_package(tmp_path):
    brief = ProjectBrief(
        project_name="POC Package",
        address="200 Main St, Example City",
        program_type="office repositioning",
        total_sf=300000,
        geography="Example City",
        comp_types=["adaptive reuse", "premium workplace"],
        amenity_priorities=["fitness_wellness", "outdoor_space"],
    )

    manifest = CompAppStages(output_root=tmp_path).run_poc(brief)

    expected = {
        "candidate_comps",
        "approved_comps",
        "comp_records_csv",
        "amenity_matrix_csv",
        "presentation_cards",
        "poc_deck",
        "deck_audit",
        "field_repair_tasks",
        "field_repair_results",
        "audit_report",
        "revision_tasks",
    }
    assert expected.issubset(manifest)
    for key in expected:
        assert Path(manifest[key]).exists()

    with open(manifest["comp_records_csv"], newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["status"] == "needs_research"


def test_user_defined_approved_comp_is_enriched_when_live_provider_available(tmp_path):
    brief = ProjectBrief(
        project_name="User Comp Enrichment",
        address="200 Main St, Example City",
        program_type="office repositioning",
        geography="Example City",
    )
    stages = CompAppStages(output_root=tmp_path)
    workspace = stages.workspace_for(brief)
    comp_id = "user-comp-660-fifth"
    write_json(
        workspace.data / "candidate_comps.json",
        [
            {
                "comp_id": comp_id,
                "comp_name": "660 Fifth Avenue",
                "location": "New York, NY",
                "comp_type": "user_defined",
                "relevance_score": 90,
                "status": "user_added",
                "known_attributes": {
                    "program_type": "office repositioning",
                    "presentation_takeaway": "User-added comparable project for office repositioning.",
                },
                "missing_attributes": ["scale", "architect_designer"],
                "source_notes": ["User-added comp."],
            }
        ],
    )
    write_json(
        workspace.data / "approved_comps.json",
        [
            {
                "comp_id": comp_id,
                "comp_name": "660 Fifth Avenue",
                "location": "New York, NY",
                "comp_type": "user_defined",
                "approval_notes": "Approved from test.",
            }
        ],
    )
    write_json(workspace.data / "source_log.json", [])

    class FakeProvider:
        def enrich_candidate(self, brief: ProjectBrief, candidate: CompCandidate) -> LiveSearchResult:
            return LiveSearchResult(
                candidates=[
                    CompCandidate(
                        comp_id=candidate.comp_id,
                        comp_name=candidate.comp_name,
                        location=candidate.location,
                        comp_type=candidate.comp_type,
                        relevance_score=95,
                        status="source_snapshot",
                        known_attributes={
                            "program_type": "office repositioning",
                            "total_sf": 1500000,
                            "completion_year": "2022",
                            "developer_owner": "Owner Example",
                            "architect_designer": "Architect Example",
                            "intervention_type": "Lobby repositioning",
                            "presentation_takeaway": "Useful repositioning precedent with sourced facts.",
                            "_sources": [
                                {
                                    "name": "Architect project page",
                                    "type": "architect/designer page",
                                    "url": "https://example.com/660",
                                    "notes": "Supports design team and scope.",
                                }
                            ],
                        },
                        source_notes=["Enriched from live search."],
                    )
                ],
                source_log=[
                    SourceLogEntry(
                        source_name="Architect project page",
                        source_type="architect/designer page",
                        url_or_search="https://example.com/660",
                        related_output="openai_live_search",
                        status="retrieved",
                    )
                ],
            )

    stages.agent.search_provider = FakeProvider()
    paths = stages.research(brief)
    records = json.loads(Path(paths["comp_records_json"]).read_text(encoding="utf-8"))

    assert records[0]["status"] == "source_snapshot"
    assert records[0]["total_sf"] == 1500000
    assert records[0]["architect_designer"] == "Architect Example"
    assert records[0]["relevance_summary"] == "Useful repositioning precedent with sourced facts."
    assert records[0]["source_count"] == 1


def test_openai_discovery_prompt_excludes_user_defined_comps():
    brief = ProjectBrief(
        project_name="Prompt Exclusion",
        address="200 Main St, Example City",
        program_type="office repositioning",
        geography="New York, NY",
        filters={
            "excluded_user_defined_comps": [
                {"name": "660 Fifth Avenue", "location": "New York, NY", "note": "User supplied"},
            ]
        },
    )

    messages = OpenAIWebSearchProvider(api_key="test")._build_input(brief, max_candidates=5)
    system_text = messages[0]["content"]
    user_payload = json.loads(messages[1]["content"])

    assert "do not return those projects" in system_text
    assert user_payload["project"]["excluded_user_defined_comps"] == [
        {"name": "660 Fifth Avenue", "location": "New York, NY", "note": "User supplied"}
    ]
    assert "Exclude any projects listed in excluded_user_defined_comps" in user_payload["task"]


def test_internal_research_status_does_not_render_as_year_status():
    normalized = normalize_comp_for_deck(
        {
            "comp_id": "test-comp",
            "project_name": "343 Madison Avenue",
            "location": "New York, NY",
            "program_type": "office tower",
            "status": "source_snapshot",
            "completion_year": "",
            "developer_owner": "BXP",
            "architect_designer": "KPF",
            "relevance_summary": "Useful precedent.",
            "confidence": "medium",
            "source_count": 1,
            "review_notes": [],
        },
        None,
        {
            "comparison_matrix_columns": ["Program"],
            "profile_adaptive_fields": [],
        },
        [],
    )

    assert normalized["status_year"] == "—"


def test_summary_matrix_uses_relevance_header():
    assert DEFAULT_SUMMARY_COLUMNS[-1] == "Relevance"
    assert "Why Relevant" not in DEFAULT_SUMMARY_COLUMNS


def test_user_defined_comp_type_does_not_render_as_intervention_strategy():
    normalized = normalize_comp_for_deck(
        {
            "comp_id": "user-comp",
            "project_name": "User Comp",
            "location": "New York, NY",
            "program_type": "office repositioning",
            "status": "source_snapshot",
            "completion_year": "2024",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "relevance_summary": "Lobby and podium repositioning precedent.",
            "confidence": "medium",
            "source_count": 1,
            "review_notes": [],
        },
        CompCandidate(
            comp_id="user-comp",
            comp_name="User Comp",
            location="New York, NY",
            comp_type="user_defined",
            relevance_score=90,
            status="source_snapshot",
            known_attributes={"program_type": "office repositioning"},
        ),
        {
            "comparison_matrix_columns": ["Program"],
            "profile_adaptive_fields": [],
        },
        [],
    )

    assert normalized["intervention_type"] != "User_Defined"
    assert normalized["intervention_type"] == "Lobby repositioning"


def test_live_and_user_defined_approved_comps_both_receive_enrichment(tmp_path):
    brief = ProjectBrief(
        project_name="All Approved Enrichment",
        address="200 Main St, Example City",
        program_type="office repositioning",
        geography="New York, NY",
    )
    stages = CompAppStages(output_root=tmp_path)
    workspace = stages.workspace_for(brief)
    write_json(
        workspace.data / "candidate_comps.json",
        [
            {
                "comp_id": "live-1",
                "comp_name": "Live Comp",
                "location": "New York, NY",
                "comp_type": "premium workplace",
                "relevance_score": 90,
                "status": "source_snapshot",
                "known_attributes": {"program_type": "office repositioning"},
                "missing_attributes": [],
                "source_notes": [],
            },
            {
                "comp_id": "user-1",
                "comp_name": "User Comp",
                "location": "New York, NY",
                "comp_type": "user_defined",
                "relevance_score": 88,
                "status": "user_added",
                "known_attributes": {"program_type": "office repositioning"},
                "missing_attributes": [],
                "source_notes": [],
            },
        ],
    )
    write_json(
        workspace.data / "approved_comps.json",
        [
            {"comp_id": "live-1", "comp_name": "Live Comp", "location": "New York, NY", "comp_type": "premium workplace"},
            {"comp_id": "user-1", "comp_name": "User Comp", "location": "New York, NY", "comp_type": "user_defined"},
        ],
    )
    write_json(workspace.data / "source_log.json", [])

    class FakeProvider:
        def __init__(self):
            self.enriched = []

        def enrich_candidate(self, brief: ProjectBrief, candidate: CompCandidate) -> LiveSearchResult:
            self.enriched.append(candidate.comp_id)
            return LiveSearchResult(candidates=[_complete_candidate(candidate)])

        def repair_candidate(self, brief: ProjectBrief, current_comp: dict, missing_fields: list[str]) -> LiveSearchResult:
            return LiveSearchResult()

    provider = FakeProvider()
    stages.agent.search_provider = provider
    stages.research(brief)

    assert set(provider.enriched) == {"live-1", "user-1"}


def test_candidate_repair_runs_only_for_incomplete_enrichment(tmp_path):
    brief = ProjectBrief(
        project_name="Conditional Repair",
        address="200 Main St, Example City",
        program_type="office repositioning",
        geography="New York, NY",
    )
    stages = CompAppStages(output_root=tmp_path)
    workspace = stages.workspace_for(brief)
    write_json(
        workspace.data / "candidate_comps.json",
        [
            {"comp_id": "complete", "comp_name": "Complete Comp", "location": "NY", "comp_type": "office", "relevance_score": 90, "status": "source_snapshot", "known_attributes": {}, "missing_attributes": [], "source_notes": []},
            {"comp_id": "incomplete", "comp_name": "Incomplete Comp", "location": "NY", "comp_type": "office", "relevance_score": 90, "status": "source_snapshot", "known_attributes": {}, "missing_attributes": [], "source_notes": []},
        ],
    )
    write_json(
        workspace.data / "approved_comps.json",
        [
            {"comp_id": "complete", "comp_name": "Complete Comp", "location": "NY", "comp_type": "office"},
            {"comp_id": "incomplete", "comp_name": "Incomplete Comp", "location": "NY", "comp_type": "office"},
        ],
    )
    write_json(workspace.data / "source_log.json", [])

    class FakeProvider:
        def __init__(self):
            self.repaired = []

        def enrich_candidate(self, brief: ProjectBrief, candidate: CompCandidate) -> LiveSearchResult:
            if candidate.comp_id == "complete":
                return LiveSearchResult(candidates=[_complete_candidate(candidate)])
            partial = CompCandidate(
                comp_id=candidate.comp_id,
                comp_name=candidate.comp_name,
                location=candidate.location,
                comp_type=candidate.comp_type,
                relevance_score=candidate.relevance_score,
                status="source_snapshot",
                known_attributes={"program_type": "office repositioning", "_sources": [{"name": "Source", "type": "official", "url": "https://example.com"}]},
                source_notes=[],
            )
            return LiveSearchResult(candidates=[partial])

        def repair_candidate(self, brief: ProjectBrief, current_comp: dict, missing_fields: list[str]) -> LiveSearchResult:
            self.repaired.append(current_comp["comp_id"])
            return LiveSearchResult(candidates=[_complete_candidate(CompCandidate(
                comp_id=current_comp["comp_id"],
                comp_name=current_comp["comp_name"],
                location=current_comp["location"],
                comp_type=current_comp["comp_type"],
                relevance_score=current_comp["relevance_score"],
                status="source_snapshot",
            ))])

    provider = FakeProvider()
    stages.agent.search_provider = provider
    stages.research(brief)

    assert provider.repaired == ["incomplete"]


def test_final_auditor_creates_tasks_for_client_facing_gaps():
    deck_data = {
        "comps": [
            {
                "project_name": "Gap Comp",
                "location": "New York, NY",
                "status_year": "—",
                "scale": {"display": "—"},
                "owner_developer": "—",
                "architect_designer": "KPF",
                "relevance_to_subject": "Useful precedent.",
                "defining_move": "Lobby repositioning.",
                "primary_sources": [],
            }
        ]
    }
    image_manifest = [{"project_name": "Gap Comp", "slots": {"overall": {"status": "saved"}}}]

    audit, tasks = _audit_deck_data(deck_data, image_manifest)

    assert any(item["field"] == "status_year" and item["status"] == "fail" for item in audit)
    assert [task["field"] for task in tasks[:4]] == ["images", "status_year", "scale", "owner_developer"]


def test_field_repair_fills_blank_but_does_not_overwrite_strong_value():
    deck_data = {
        "comps": [
            {
                "project_name": "Repair Comp",
                "scale": {"display": "930,000 SF"},
                "status_year": "—",
                "primary_sources": [],
            }
        ]
    }

    _apply_field_repair(deck_data, {"project_name": "Repair Comp", "field": "scale"}, {"value": "1.2M SF", "confidence": "high", "sources": []})
    _apply_field_repair(deck_data, {"project_name": "Repair Comp", "field": "status_year"}, {"value": "2026", "confidence": "medium", "sources": []})

    comp = deck_data["comps"][0]
    assert comp["scale"]["display"] == "930,000 SF"
    assert comp["status_year"] == "2026"


def test_lobby_repositioning_uses_intent_led_adaptive_fields():
    brief = ProjectBrief(
        project_name="200 Vesey",
        address="200 Vesey Street, New York, NY",
        program_type="lobby repositioning",
        geography="New York, NY",
        comp_types=["podium renovation", "tower repositioning"],
    )

    strategy = build_deck_strategy(brief, [])

    assert strategy["profile_adaptive_fields"] == [
        "Podium / Base Strategy",
        "Arrival / Lobby Move",
        "Amenity / User Experience",
        "Street / Public Interface",
        "Positioning Intent",
    ]
    assert strategy["deck_title"] == "Comparative Projects"
    assert strategy["cover_intent_label"] == "Offices in New York"


def test_residential_house_uses_house_adaptive_fields():
    brief = ProjectBrief(
        project_name="Hill House",
        address="Example Road",
        program_type="single family residential house",
        geography="California",
    )

    strategy = build_deck_strategy(brief, [])

    assert strategy["profile_adaptive_fields"] == [
        "Site Relationship",
        "Material Strategy",
        "Indoor / Outdoor Living",
        "Plan Organization",
        "Privacy / Views",
    ]


def test_civic_landmark_uses_civic_adaptive_fields():
    brief = ProjectBrief(
        project_name="Civic Landmark Study",
        address="Main Plaza",
        program_type="civic landmark expansion",
        geography="Boston",
    )

    strategy = build_deck_strategy(brief, [])

    assert "Civic Role" in strategy["profile_adaptive_fields"]
    assert "Public Access" in strategy["profile_adaptive_fields"]
    assert "Symbolic Identity" in strategy["profile_adaptive_fields"]
    assert strategy["cover_intent_label"] == "Public Landmarks in Boston"


def test_adaptive_facts_prefer_exact_labels_and_avoid_universal_duplicates():
    normalized = normalize_comp_for_deck(
        {
            "comp_id": "test",
            "project_name": "Lobby Comp",
            "location": "New York, NY",
            "program_type": "office repositioning",
            "status": "source_snapshot",
            "completion_year": "2026",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "relevance_summary": "Useful precedent.",
            "confidence": "medium",
            "source_count": 1,
            "review_notes": [],
        },
        CompCandidate(
            comp_id="test",
            comp_name="Lobby Comp",
            location="New York, NY",
            comp_type="lobby repositioning",
            relevance_score=90,
            status="source_snapshot",
            known_attributes={
                "program_type": "office repositioning",
                "developer_owner": "Owner",
                "adaptive_fields": {
                    "Podium / Base Strategy": "Reworked podium glazing and entry expression",
                    "Arrival / Lobby Move": "Hospitality-scaled arrival sequence",
                    "Positioning Intent": "Owner",
                },
            },
        ),
        {
            "comparison_matrix_columns": ["Program"],
            "profile_adaptive_fields": ["Podium / Base Strategy", "Arrival / Lobby Move", "Positioning Intent"],
        },
        [],
    )

    assert normalized["adaptive_fields"]["Podium / Base Strategy"] == "Reworked podium glazing and entry expression"
    assert normalized["adaptive_fields"]["Arrival / Lobby Move"] == "Hospitality-scaled arrival sequence"
    assert "Positioning Intent" not in normalized["adaptive_fields"]


def test_adaptive_facts_keep_useful_repaired_labels_when_profile_labels_mismatch():
    normalized = normalize_comp_for_deck(
        {
            "comp_id": "test",
            "project_name": "Repaired Comp",
            "location": "New York, NY",
            "program_type": "office repositioning",
            "status": "source_snapshot",
            "completion_year": "2026",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "relevance_summary": "Useful precedent.",
            "confidence": "medium",
            "source_count": 2,
            "review_notes": [],
        },
        CompCandidate(
            comp_id="test",
            comp_name="Repaired Comp",
            location="New York, NY",
            comp_type="office repositioning",
            relevance_score=90,
            status="source_snapshot",
            known_attributes={
                "program_type": "office repositioning",
                "adaptive_fields": {
                    "Arrival Sequence": "Layered street-to-lobby arrival with hospitality cues.",
                    "Amenity Stack": "Tenant lounge, conference space, and wellness program.",
                    "Site Integration": "Improved base condition and stronger connection to transit.",
                },
            },
        ),
        {
            "comparison_matrix_columns": ["Program"],
            "profile_adaptive_fields": [
                "Podium / Base Strategy",
                "Arrival / Lobby Move",
                "Amenity / User Experience",
                "Street / Public Interface",
                "Positioning Intent",
            ],
        },
        [],
    )

    assert normalized["adaptive_fields"]["Arrival Sequence"].startswith("Layered street-to-lobby")
    assert normalized["adaptive_fields"]["Amenity Stack"].startswith("Tenant lounge")
    assert normalized["adaptive_fields"]["Site Integration"].startswith("Improved base")


def test_adaptive_fact_values_are_shortened_without_ellipsis():
    normalized = normalize_comp_for_deck(
        {
            "comp_id": "test",
            "project_name": "Long Adaptive Comp",
            "location": "New York, NY",
            "program_type": "office repositioning",
            "status": "source_snapshot",
            "completion_year": "2026",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "relevance_summary": "Useful precedent.",
            "confidence": "medium",
            "source_count": 1,
            "review_notes": [],
        },
        CompCandidate(
            comp_id="test",
            comp_name="Long Adaptive Comp",
            location="New York, NY",
            comp_type="lobby repositioning",
            relevance_score=90,
            status="source_snapshot",
            known_attributes={
                "program_type": "office repositioning",
                "adaptive_fields": {
                    "Podium / Base Strategy": "Glass-canopy garden and more transparent ground level reframe the landmark base while improving public visibility and arrival clarity.",
                },
            },
        ),
        {
            "comparison_matrix_columns": ["Program"],
            "profile_adaptive_fields": ["Podium / Base Strategy"],
        },
        [],
    )

    value = normalized["adaptive_fields"]["Podium / Base Strategy"]
    assert "…" not in value
    lines = value.splitlines()
    assert 1 <= len(lines) <= 2
    assert all(len(line) <= 54 for line in lines)
    assert len(lines[-1]) >= 28


def test_profile_title_is_limited_to_two_compact_lines():
    title = _profile_title_text("Very Long Comparative Project Name With Extra Parenthetical Descriptor And Market Context")
    lines = title.splitlines()

    assert len(lines) <= 2
    assert all(len(line) <= 34 for line in lines)


def test_profile_tags_prefer_key_features_over_generic_labels():
    tags = _profile_tags(
        {
            "project_type": "Office",
            "intervention_type": "Lobby repositioning",
            "key_program": "Workplace",
            "adaptive_fields": {"Arrival / Lobby Move": "Hospitality arrival"},
            "comparison_flags": {
                "Tenant Lounge": "●",
                "Terraces": "●",
                "Cafe": "●",
                "Restaurant": "—",
            },
        }
    )

    assert tags[:3] == ["Lounges", "Terraces", "Cafe"]
    assert "Office" not in tags[:3]
    assert "Lobby repositioning" not in tags[:3]


def test_design_levers_are_dynamic_from_feature_frequency():
    deck_data = {
        "deck_strategy": {
            "comparison_matrix_columns": [
                "Terraces",
                "Public Plaza",
                "Retail",
                "Transit Connection",
                "Tenant Lounge",
                "Fitness / Wellness",
            ]
        },
        "comps": [
            {
                "comparison_flags": {
                    "Terraces": "●",
                    "Public Plaza": "●",
                    "Retail": "●",
                    "Transit Connection": "●",
                    "Tenant Lounge": "—",
                    "Fitness / Wellness": "—",
                }
            }
            for _ in range(4)
        ]
        + [
            {
                "comparison_flags": {
                    "Terraces": "●",
                    "Public Plaza": "●",
                    "Retail": "—",
                    "Transit Connection": "—",
                    "Tenant Lounge": "●",
                    "Fitness / Wellness": "●",
                }
            }
            for _ in range(2)
        ],
    }

    groups = _design_lever_groups(deck_data)
    titles = [group["title"] for group in groups]

    assert titles[0] in {"Outdoor Amenity Value", "Public-Facing Activation"}
    assert "Outdoor Amenity Value" in titles
    assert "Public-Facing Activation" in titles
    assert len(groups) <= 5
    assert all(group["features"] for group in groups)


def test_comparison_columns_are_selected_from_enriched_adaptive_facts():
    brief = ProjectBrief(
        project_name="Dynamic Matrix",
        address="200 Main St",
        program_type="office repositioning",
        geography="New York, NY",
        design_priorities=["arrival experience", "tenant lounge", "terraces", "food and beverage"],
        comp_guidance="Prioritize cafe, restaurant, sky lobby, lounge spaces, co-working, terraces, and branded amenities.",
    )
    candidate = CompCandidate(
        comp_id="comp-1",
        comp_name="Comp One",
        location="New York, NY",
        comp_type="office",
        relevance_score=90,
        status="source_snapshot",
        known_attributes={
            "program_type": "office repositioning",
            "adaptive_fields": {
                "Arrival / Lobby Move": "Hospitality-scaled arrival with lobby seating and a sky lobby.",
                "Street / Public Interface": "Transparent public frontage with a public plaza and retail.",
                "Amenity / User Experience": "Tenant lounge, co-working, terraces, cafe, restaurant, and branded amenities.",
            },
        },
    )
    records = [
        {
            "comp_id": "comp-1",
            "project_name": "Comp One",
            "location": "New York, NY",
            "program_type": "office repositioning",
            "status": "source_snapshot",
            "completion_year": "2026",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "relevance_summary": "Useful precedent with a tenant lounge, roof terrace, cafe, restaurant, and public plaza.",
            "confidence": "medium",
            "source_count": 1,
            "review_notes": [],
        }
    ]

    deck_data = build_comp_study_deck_data(brief, records, [candidate], [])

    columns = deck_data["deck_strategy"]["comparison_matrix_columns"]
    assert "Tenant Lounge" in columns
    assert "Terraces" in columns
    assert "Cafe" in columns
    assert "Restaurant" in columns
    assert "Arrival / Lobby Move" not in columns
    assert deck_data["comps"][0]["comparison_flags"]["Tenant Lounge"] == "●"


def test_feature_matrix_normalizes_specific_amenities():
    brief = ProjectBrief(
        project_name="Feature Normalization",
        address="200 Main St",
        program_type="office repositioning",
        geography="New York, NY",
        design_priorities=["tenant lounge", "terraces", "cafe", "restaurant", "food hall"],
    )
    records = [
        {
            "comp_id": "comp-1",
            "project_name": "Comp One",
            "location": "New York, NY",
            "program_type": "office repositioning",
            "status": "source_snapshot",
            "completion_year": "2026",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "relevance_summary": "Club lounge, lounge spaces, roof terrace, outdoor deck, cafe, restaurant, and food hall.",
            "confidence": "medium",
            "source_count": 1,
            "review_notes": ["Tenant lounge and terraces verified."],
        }
    ]

    deck_data = build_comp_study_deck_data(brief, records, [], [])
    columns = deck_data["deck_strategy"]["comparison_matrix_columns"]

    assert "Tenant Lounge" in columns
    assert "Terraces" in columns
    assert "Cafe" in columns
    assert "Restaurant" in columns
    assert "Food Hall" in columns


def test_feature_matrix_uses_high_resolution_column_count():
    brief = ProjectBrief(
        project_name="High Resolution Matrix",
        address="200 Main St",
        program_type="office repositioning",
        geography="New York, NY",
        design_priorities=["arrival", "tenant amenities", "public realm"],
        comp_guidance="Cafe, restaurant, food hall, sky lobby, lobby seating, tenant lounge, co-working, conference center, wellness, terraces, retail, public plaza, branded amenities, art, transit, sustainability.",
    )
    features = "Cafe restaurant food hall sky lobby lobby seating tenant lounge co-working conference center fitness wellness terraces retail public plaza branded amenities art installations transit sustainability."
    records = [
        {
            "comp_id": f"comp-{index}",
            "project_name": f"Comp {index}",
            "location": "New York, NY",
            "program_type": "office repositioning",
            "status": "source_snapshot",
            "completion_year": "2026",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "relevance_summary": features,
            "confidence": "medium",
            "source_count": 1,
            "review_notes": [],
        }
        for index in range(3)
    ]

    deck_data = build_comp_study_deck_data(brief, records, [], [])
    columns = deck_data["deck_strategy"]["comparison_matrix_columns"]

    assert 10 <= len(columns) <= FEATURE_MATRIX_MAX_COLUMNS
    assert not (set(columns) & GENERIC_FEATURE_COLUMNS)
    assert "Cafe" in columns
    assert "Restaurant" in columns
    assert "Tenant Lounge" in columns
    assert "Terraces" in columns
    assert "Public Plaza" in columns


def test_custom_comparison_matrix_deck_smoke(tmp_path):
    columns = [
        "Cafe",
        "Restaurant",
        "Food Hall",
        "Sky Lobby",
        "Lobby Seating",
        "Tenant Lounge",
        "Co-working",
        "Conference Center",
        "Fitness / Wellness",
        "Terraces",
        "Retail",
        "Public Plaza",
        "Branded Amenities",
        "Art / Installations",
        "Transit Connection",
    ]
    deck_data = {
        "subject_project": {"address": "200 Main St", "city": "New York", "state_or_country": "NY"},
        "deck_strategy": {
            "deck_title": "Comparative Projects",
            "project_type_label": "Office Repositioning",
            "cover_intent_label": "Offices in New York",
            "study_focus_label": "Comparable project study for early concept positioning",
            "summary_matrix_columns": DEFAULT_SUMMARY_COLUMNS,
            "comparison_matrix_columns": columns,
        },
        "comps": [
            {
                "project_name": "Comp One",
                "location": "New York, NY",
                "project_type": "Office",
                "scale": {"display": "1.0M SF"},
                "status_year": "2026",
                "owner_developer": "Owner",
                "architect_designer": "Architect",
                "intervention_type": "Lobby repositioning",
                "key_program": "Workplace",
                "adaptive_fields": {},
                "comparison_flags": {column: "●" for column in columns},
                "relevance_to_subject": "Relevant precedent.",
                "hero_image": {"path": "", "url": "", "caption": "", "credit": "", "source_url": "", "image_confidence": "not_available"},
                "image_package": {"overall": {}, "focus": {}, "detail": {}},
                "primary_sources": [],
                "data_confidence": "medium",
                "selection_reasoning_internal": "",
                "diligence_notes_internal": [],
                "defining_move": "Lobby repositioning",
            }
        ],
        "takeaways": [{"trend": "Trend.", "implication": "Implication."}],
    }

    output = create_concept_deck_from_data(deck_data, tmp_path / "matrix.pptx")

    assert output.exists()


def test_comparison_matrix_paginates_by_fixed_row_capacity(tmp_path):
    columns = ["Arrival / Lobby Move", "Amenity / User Experience", "Street / Public Interface"]
    comps = []
    for index in range(16):
        comps.append(
            {
                "project_name": f"Comp {index + 1}",
                "location": "New York, NY",
                "project_type": "Office",
                "scale": {"display": "1.0M SF"},
                "status_year": "2026",
                "owner_developer": "Owner",
                "architect_designer": "Architect",
                "intervention_type": "Lobby repositioning",
                "key_program": "Workplace",
                "adaptive_fields": {},
                "comparison_flags": {column: "●" for column in columns},
                "relevance_to_subject": "Relevant precedent.",
                "hero_image": {"path": "", "url": "", "caption": "", "credit": "", "source_url": "", "image_confidence": "not_available"},
                "image_package": {"overall": {}, "focus": {}, "detail": {}},
                "primary_sources": [],
                "data_confidence": "medium",
                "selection_reasoning_internal": "",
                "diligence_notes_internal": [],
                "defining_move": "Lobby repositioning",
            }
        )
    deck_data = {
        "subject_project": {"address": "200 Main St", "city": "New York", "state_or_country": "NY"},
        "deck_strategy": {
            "deck_title": "Comparative Projects",
            "project_type_label": "Office Repositioning",
            "cover_intent_label": "Offices in New York",
            "study_focus_label": "Comparable project study for early concept positioning",
            "summary_matrix_columns": DEFAULT_SUMMARY_COLUMNS,
            "comparison_matrix_columns": columns,
        },
        "comps": comps,
        "takeaways": [{"trend": "Trend.", "implication": "Implication."}],
    }

    output = create_concept_deck_from_data(deck_data, tmp_path / "paginated-matrix.pptx")

    assert output.exists()


def test_matrix_title_and_header_wrapping_are_presentation_friendly():
    assert _matrix_title({"deck_strategy": {"project_type_label": "Office Repositioning"}}) == "Features and Amenities Matrix"
    label = _wrap_matrix_label("Art / Installations", 0.62)

    assert label == "Art"
    assert _wrap_matrix_label("Lobby Seating", 0.62) == "Lobby\nSeating"
    assert _wrap_matrix_label("Sky Lobby", 0.62) == "Sky\nLobbies"
    assert _wrap_matrix_label("Co-working", 0.62) == "Cowork"
    assert _compact_feature_label("Tenant Lounge") == "Lounges"
    assert _compact_feature_label("Lobby Seating") == "Seating"
    assert _compact_feature_label("Fitness / Wellness") == "Wellness"
    assert _compact_feature_label("Conference Center") == "Meetings"
    assert _wrap_matrix_label("Tenant Lounge", 0.62) == "Tenant\nLounges"
    assert _wrap_matrix_label("Food Hall", 0.62) == "Food\nHalls"
    assert _compact_feature_label("Food Hall") == "Food Halls"
    assert _compact_feature_label("Sustainability") == "Green"
    assert _compact_feature_label("Concierge / Hospitality") == "Concierge"
    wrapped_labels = [_wrap_matrix_label(item, 0.62) for item in ["Art / Installations", "Co-working", "Fitness / Wellness", "Food Hall", "Public Realm", "Lobby Seating"]]
    assert all("-" not in item for item in wrapped_labels)
    assert all("/n" not in item and "\\n" not in item for item in wrapped_labels)
    assert _matrix_header_shared_font_size(wrapped_labels, 0.62) == 5
    assert _matrix_header_shared_font_size(["Cafe", "Retail"], 0.9) == 7


def test_takeaways_are_trend_only_and_client_facing():
    brief = ProjectBrief(
        project_name="Takeaway Study",
        address="200 Main St",
        program_type="office repositioning",
        geography="New York, NY",
    )
    columns = ["Lobby Seating", "Tenant Lounge", "Fitness / Wellness", "Terraces", "Retail", "Public Plaza"]
    records = []
    candidates = []
    for index in range(4):
        comp_id = f"comp-{index}"
        records.append(
            {
                "comp_id": comp_id,
                "project_name": f"Comp {index}",
                "location": "New York, NY",
                "program_type": "office repositioning",
                "status": "source_snapshot",
                "completion_year": "2026",
                "developer_owner": "Owner",
                "architect_designer": "Architect",
                "relevance_summary": "Lobby, amenity, street, outdoor terrace, and premium positioning precedent.",
                "confidence": "medium",
                "source_count": 1,
                "review_notes": [],
            }
        )
        candidates.append(
            CompCandidate(
                comp_id=comp_id,
                comp_name=f"Comp {index}",
                location="New York, NY",
                comp_type="office",
                relevance_score=90,
                status="source_snapshot",
                known_attributes={
                    "program_type": "office repositioning",
                    "intervention_type": "Lobby repositioning",
                    "adaptive_fields": {
                        "Lobby Seating": "Hospitality arrival seating",
                        "Tenant Lounge": "Tenant lounge",
                        "Fitness / Wellness": "Wellness program",
                        "Terraces": "Terrace and garden",
                        "Retail": "Retail activation",
                        "Public Plaza": "Transparent frontage and public plaza",
                    },
                },
            )
        )

    deck_data = build_comp_study_deck_data(brief, records, candidates, [])
    trends = [item["trend"] for item in deck_data["takeaways"]]

    assert deck_data["takeaway_summary"]
    assert len(trends) >= 5
    assert all("implication" not in item for item in deck_data["takeaways"])
    assert not any("public web sources" in trend.lower() or "verify" in trend.lower() for trend in trends)
    assert any("activated lobby spaces" in trend for trend in trends)
    assert any("terraces with adjacent amenity program" in trend for trend in trends)
    assert any("standard offering" in trend for trend in trends)


def _complete_candidate(candidate: CompCandidate) -> CompCandidate:
    return CompCandidate(
        comp_id=candidate.comp_id,
        comp_name=candidate.comp_name,
        location=candidate.location,
        comp_type=candidate.comp_type,
        relevance_score=candidate.relevance_score,
        status="source_snapshot",
        known_attributes={
            "program_type": "office repositioning",
            "total_sf": 930000,
            "completion_year": "2026",
            "developer_owner": "Owner",
            "architect_designer": "Architect",
            "intervention_type": "Lobby repositioning",
            "presentation_takeaway": "Useful sourced precedent.",
            "_sources": [
                {"name": "Source 1", "type": "official", "url": "https://example.com/1"},
                {"name": "Source 2", "type": "architect", "url": "https://example.com/2"},
                {"name": "Source 3", "type": "publication", "url": "https://example.com/3"},
            ],
            "adaptive_fields": {
                "Podium / Base Strategy": "Reworked base",
                "Arrival / Lobby Move": "Hospitality arrival",
                "Street / Public Interface": "Transparent frontage",
            },
            "image_candidates": [
                {"url": "https://example.com/1.jpg"},
                {"url": "https://example.com/2.jpg"},
                {"url": "https://example.com/3.jpg"},
            ],
        },
        source_notes=["Complete evidence package."],
    )
