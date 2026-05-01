from __future__ import annotations

import re
from typing import Any

from comp_agent.models import CompCandidate, ProjectBrief, SourceLogEntry


DEFAULT_SUMMARY_COLUMNS = [
    "Project",
    "Location",
    "Type",
    "Scale",
    "Year / Status",
    "Intervention / Strategy",
    "Relevance",
]

COMPARISON_COLUMNS_BY_TYPE = {
    "office": ["Lobby Repositioning", "Tenant Amenities", "F&B", "Outdoor Space", "Public Realm", "Transit Connection", "Retail Activation"],
    "residential": ["Units", "Unit Mix", "Amenity Deck", "Outdoor Space", "Co-working", "Retail", "Parking", "Affordability"],
    "hotel": ["Keys", "Brand / Flag", "Restaurant / Bar", "Rooftop", "Spa / Wellness", "Meeting / Event Space", "Arrival"],
    "mixed": ["Residential", "Hotel", "Office", "Retail", "Public Realm", "Podium Strategy", "Vertical Stacking", "Shared Amenities"],
    "adaptive": ["Original Use", "New Use", "Structure Retained", "Facade Retained", "Added Area", "Public Access", "Sustainability Claim"],
    "institutional": ["Program", "Gathering Space", "Public Interface", "Campus Gateway", "Expansion", "Flexible Space", "Landscape"],
    "campus": ["Program", "Gathering Space", "Public Interface", "Campus Gateway", "Expansion", "Flexible Space", "Landscape"],
    "life science": ["Lab Area", "Lab Type", "Mechanical Strategy", "Floor Loading", "Vibration Criteria", "Collaboration", "Spec / Tenant"],
    "public realm": ["Public Access", "Landscape", "Programming", "Retail Edge", "Transit", "Civic Identity", "Open Space"],
    "retail": ["Retail Area", "F&B", "Anchor Tenants", "Street Edge", "Public Realm", "Adaptive Reuse", "Destination Strategy"],
}

PROFILE_FIELDS_BY_TYPE = {
    "office": ["Lobby Strategy", "Amenity Package", "Public Realm Interface", "Transit Connection", "Retail Activation"],
    "residential": ["Units", "Unit Mix", "Amenity Package", "Ground-Floor Program", "Target Resident"],
    "hotel": ["Keys", "Brand / Flag", "F&B", "Wellness / Spa", "Event Space"],
    "mixed": ["Use Mix", "Vertical Stacking", "Podium Strategy", "Public Realm Strategy", "Shared Amenities"],
    "adaptive": ["Original Use", "New Use", "Preservation Strategy", "Added Area", "Sustainability Claim"],
    "institutional": ["Campus Role", "Program Type", "Gathering Spaces", "Public Interface", "Expansion Strategy"],
    "campus": ["Campus Role", "Program Type", "Gathering Spaces", "Public Interface", "Expansion Strategy"],
    "life science": ["Lab Area", "Lab Type", "Mechanical Strategy", "Collaboration Spaces", "Spec / Tenant"],
    "public realm": ["Public Access", "Landscape Strategy", "Programming", "Street Edge", "Civic Role"],
    "retail": ["Retail Area", "F&B", "Street Edge", "Tenant Mix", "Destination Strategy"],
}

INTENT_ADAPTIVE_FIELDS = [
    (
        ("lobby", "reposition", "podium", "base"),
        ["Podium / Base Strategy", "Arrival / Lobby Move", "Amenity / User Experience", "Street / Public Interface", "Positioning Intent"],
    ),
    (
        ("house", "single family", "residential house", "villa"),
        ["Site Relationship", "Material Strategy", "Indoor / Outdoor Living", "Plan Organization", "Privacy / Views"],
    ),
    (
        ("civic", "landmark", "museum", "library", "cultural"),
        ["Civic Role", "Public Access", "Symbolic Identity", "Preservation / Expansion Move", "Gathering Spaces"],
    ),
    (
        ("new construction", "tower", "high-rise", "high rise"),
        ["Massing / Identity", "Arrival Sequence", "Amenity Stack", "Site Integration", "Market Positioning"],
    ),
    (
        ("adaptive reuse", "conversion", "reuse"),
        ["Original Use", "New Use", "Preservation / Structure Move", "Program Transformation", "Sustainability Claim"],
    ),
]

ADAPTIVE_FIELD_ALIASES = {
    "Podium / Base Strategy": ["podium_base_strategy", "podium_strategy", "base_strategy", "podium", "base_repositioning", "facade_strategy"],
    "Arrival / Lobby Move": ["arrival_lobby_move", "lobby_strategy", "arrival_sequence", "arrival_experience", "lobby_move"],
    "Amenity / User Experience": ["amenity_user_experience", "amenity_package", "tenant_amenities", "user_experience", "tenant_experience"],
    "Street / Public Interface": ["street_public_interface", "street_edge", "public_realm_interface", "public_interface", "ground_plane", "retail_activation"],
    "Positioning Intent": ["positioning_intent", "market_positioning", "workplace_positioning", "target_positioning"],
    "Site Relationship": ["site_relationship", "site_strategy", "landscape_relationship", "context_response"],
    "Material Strategy": ["material_strategy", "materiality", "facade_materials"],
    "Indoor / Outdoor Living": ["indoor_outdoor_living", "indoor_outdoor", "outdoor_space", "terrace_strategy"],
    "Plan Organization": ["plan_organization", "planning_strategy", "layout_strategy"],
    "Privacy / Views": ["privacy_views", "privacy_strategy", "view_strategy"],
    "Symbolic Identity": ["symbolic_identity", "identity_strategy", "civic_identity"],
    "Preservation / Expansion Move": ["preservation_expansion_move", "preservation_strategy", "expansion_strategy", "addition_strategy"],
    "Massing / Identity": ["massing_identity", "massing_strategy", "identity_strategy", "tower_identity"],
    "Arrival Sequence": ["arrival_sequence", "arrival_experience", "lobby_strategy"],
    "Amenity Stack": ["amenity_stack", "amenity_package", "tenant_amenities"],
    "Site Integration": ["site_integration", "transit_connection", "public_realm_interface"],
    "Preservation / Structure Move": ["preservation_structure_move", "preservation_strategy", "retained_structure", "retained_facade"],
    "Program Transformation": ["program_transformation", "new_use", "program_strategy"],
    "Lobby Strategy": ["lobby_strategy", "lobby", "arrival_sequence", "arrival_experience", "base_strategy"],
    "Amenity Package": ["amenity_package", "tenant_amenities", "amenities", "wellness", "tenant_experience"],
    "Public Realm Interface": ["public_realm_interface", "public_realm_strategy", "street_edge", "plaza_strategy", "ground_plane"],
    "Transit Connection": ["transit_connection", "transit_access", "subway_connection", "station_connection"],
    "Retail Activation": ["retail_activation", "ground_floor_retail", "ground_floor_program", "food_beverage", "f_b"],
    "Units": ["unit_count", "units", "residential_units"],
    "Unit Mix": ["unit_mix", "residential_mix"],
    "Ground-Floor Program": ["ground_floor_program", "retail_activation", "street_edge"],
    "Target Resident": ["target_resident", "target_resident_profile", "resident_profile"],
    "Keys": ["hotel_keys", "keys"],
    "Brand / Flag": ["brand_flag", "flag", "hotel_brand"],
    "F&B": ["food_beverage", "f_b", "restaurant_bar", "restaurant"],
    "Wellness / Spa": ["spa_wellness", "wellness", "spa"],
    "Event Space": ["meeting_event_space", "event_space", "meeting_space"],
    "Use Mix": ["use_mix", "program_mix", "mixed_use_program"],
    "Vertical Stacking": ["vertical_stacking", "stacking_strategy"],
    "Podium Strategy": ["podium_strategy", "base_strategy"],
    "Public Realm Strategy": ["public_realm_strategy", "public_realm_interface", "plaza_strategy"],
    "Shared Amenities": ["shared_amenities", "amenity_package"],
    "Original Use": ["original_use", "existing_use"],
    "New Use": ["new_use", "converted_use"],
    "Preservation Strategy": ["preservation_strategy", "retained_structure", "retained_facade"],
    "Added Area": ["added_area", "expansion_area"],
    "Sustainability Claim": ["sustainability_claim", "embodied_carbon_claim", "sustainability_certification"],
    "Campus Role": ["campus_role", "institutional_role"],
    "Program Type": ["program_type", "academic_program", "institutional_program"],
    "Gathering Spaces": ["gathering_spaces", "collaboration_spaces", "public_spaces"],
    "Public Interface": ["public_interface", "public_private_threshold", "public_realm_interface"],
    "Expansion Strategy": ["expansion_strategy", "campus_expansion"],
    "Lab Area": ["lab_area"],
    "Lab Type": ["lab_type"],
    "Mechanical Strategy": ["mechanical_strategy"],
    "Collaboration Spaces": ["collaboration_spaces", "gathering_spaces"],
    "Spec / Tenant": ["speculative_or_tenant_specific", "spec_tenant", "tenant_specific"],
    "Landscape Strategy": ["landscape_strategy", "public_realm_strategy"],
    "Programming": ["programming", "public_programming"],
    "Street Edge": ["street_edge", "ground_floor_program", "retail_activation"],
    "Civic Role": ["civic_role", "campus_role"],
    "Retail Area": ["retail_area"],
    "Tenant Mix": ["tenant_mix", "retail_mix"],
    "Destination Strategy": ["destination_strategy", "market_positioning"],
    "Program Strategy": ["program_strategy", "key_program", "program_type"],
    "Amenity Strategy": ["amenity_strategy", "amenity_package", "tenant_amenities"],
    "Market Positioning": ["market_positioning", "workplace_positioning", "target_positioning"],
}


def build_comp_study_deck_data(
    brief: ProjectBrief,
    records: list[dict[str, Any]],
    candidates: list[CompCandidate],
    source_log: list[SourceLogEntry],
) -> dict[str, Any]:
    candidate_by_id = {candidate.comp_id: candidate for candidate in candidates}
    strategy = build_deck_strategy(brief, records)
    comps = [
        normalize_comp_for_deck(record, candidate_by_id.get(str(record.get("comp_id"))), strategy, source_log)
        for record in records
    ]
    strategy["comparison_matrix_columns"] = _dynamic_comparison_columns(strategy["comparison_matrix_columns"], comps)
    for comp in comps:
        comp["comparison_flags"] = _comparison_flags_from_comp(strategy["comparison_matrix_columns"], comp)
    deck_data = {
        "subject_project": {
            "name": brief.project_name,
            "address": brief.address,
            "city": _city_from_location(brief.geography),
            "state_or_country": _state_from_location(brief.geography),
            "project_type": brief.comp_types or [brief.program_type],
            "stage": "early concept positioning",
            "search_intent": brief.program_type,
            "comparison_priorities": brief.presentation_priorities,
            "geography": brief.geography,
            "time_horizon_years": brief.time_horizon_years,
        },
        "deck_strategy": strategy,
        "comps": comps,
        "takeaway_summary": build_takeaway_summary(brief, comps, strategy),
        "takeaways": build_takeaways(brief, comps, strategy),
    }
    return deck_data


def build_deck_strategy(brief: ProjectBrief, records: list[dict[str, Any]]) -> dict[str, Any]:
    family = _project_family(brief.program_type.lower()) or _project_family(" ".join(brief.comp_types).lower()) or _project_family(
        " ".join(str(record.get("program_type", "")) for record in records).lower()
    )
    label = _title_label(brief.program_type or (brief.comp_types[0] if brief.comp_types else "Comp Study"))
    adaptive_fields = _dynamic_profile_adaptive_fields(brief, records, family)
    return {
        "deck_title": "Comparative Projects",
        "project_type_label": label,
        "study_focus_label": "Comparable project study for early concept positioning",
        "cover_intent_label": _cover_intent_label(brief, family),
        "summary_matrix_columns": DEFAULT_SUMMARY_COLUMNS,
        "comparison_matrix_columns": COMPARISON_COLUMNS_BY_TYPE.get(family, ["Program", "Scale", "Public Interface", "Amenities", "Adaptive Reuse", "Market Positioning"]),
        "profile_adaptive_fields": adaptive_fields,
    }


def normalize_comp_for_deck(
    record: dict[str, Any],
    candidate: CompCandidate | None,
    strategy: dict[str, Any],
    source_log: list[SourceLogEntry],
) -> dict[str, Any]:
    attrs = candidate.known_attributes if candidate else {}
    sources = _candidate_sources(attrs, record, source_log)
    project_type = _project_type(attrs, record, candidate)
    intervention = _intervention_type(attrs, record, candidate, project_type)
    scale = _scale(record, attrs)
    adaptive_fields = _adaptive_fields(attrs, record, strategy)
    hero_image = attrs.get("hero_image") if isinstance(attrs.get("hero_image"), dict) else {}
    image_package = attrs.get("image_package") if isinstance(attrs.get("image_package"), dict) else {}
    normalized_hero = {
        "path": "",
        "url": _clean(hero_image.get("url")),
        "caption": _clean(hero_image.get("caption")),
        "credit": _clean(hero_image.get("credit")),
        "source_url": _clean(hero_image.get("source_url")) or (sources[0]["url"] if sources else ""),
        "image_confidence": _clean(hero_image.get("image_confidence")) or "not_available",
        "fallback_urls": hero_image.get("fallback_urls") or [],
        "fallback_used": False,
    }
    return {
        "project_name": _clean(record.get("project_name")),
        "location": _clean(record.get("location")),
        "city": _city_from_location(record.get("location", "")),
        "state_or_country": _state_from_location(record.get("location", "")),
        "project_type": project_type or "—",
        "scale": scale,
        "status_year": _status_year(record, attrs),
        "owner_developer": _clean(record.get("developer_owner") or attrs.get("developer_owner")) or "—",
        "architect_designer": _clean(record.get("architect_designer") or attrs.get("architect_designer")) or "—",
        "intervention_type": intervention,
        "key_program": _key_program(project_type, attrs, record),
        "defining_move": _defining_move(record, attrs),
        "relevance_to_subject": _relevance(record, attrs),
        "hero_image": normalized_hero,
        "image_package": _normalize_image_package(image_package, normalized_hero),
        "image_candidates": attrs.get("image_candidates") if isinstance(attrs.get("image_candidates"), list) else [],
        "primary_sources": sources[:4],
        "data_confidence": _confidence(record, sources),
        "adaptive_fields": adaptive_fields,
        "comparison_flags": _comparison_flags(strategy["comparison_matrix_columns"], attrs, record),
        "selection_reasoning_internal": _selection_reasoning(candidate, record),
        "diligence_notes_internal": list(record.get("review_notes") or []) + list(candidate.missing_attributes if candidate else []),
    }


def _normalize_image_package(image_package: dict[str, Any], hero_image: dict[str, Any]) -> dict[str, Any]:
    package = {"overall": {}, "focus": {}, "detail": {}}
    for slot in package:
        raw = image_package.get(slot) if isinstance(image_package.get(slot), dict) else {}
        package[slot] = {
            "slot": slot,
            "path": _clean(raw.get("path")),
            "url": _clean(raw.get("url")),
            "source_url": _clean(raw.get("source_url")),
            "caption": _clean(raw.get("caption")),
            "credit": _clean(raw.get("credit")),
            "confidence": _clean(raw.get("confidence") or raw.get("image_confidence")) or "not_available",
            "selection_reason": _clean(raw.get("selection_reason")),
        }
    if not package["overall"]["url"] and hero_image.get("url"):
        package["overall"].update(
            {
                "url": hero_image.get("url", ""),
                "source_url": hero_image.get("source_url", ""),
                "caption": hero_image.get("caption", ""),
                "credit": hero_image.get("credit", ""),
                "confidence": hero_image.get("image_confidence", "medium"),
                "selection_reason": "Carried forward from hero image candidate.",
            }
        )
    return package


def build_takeaway_summary(brief: ProjectBrief, comps: list[dict[str, Any]], strategy: dict[str, Any]) -> str:
    type_label = strategy.get("project_type_label", "Comparable projects")
    common_features = _ranked_feature_counts(comps, strategy.get("comparison_matrix_columns", []))
    top_features = [label for label, count in common_features[:3] if count]
    if top_features:
        feature_text = ", ".join(top_features)
        return f"{type_label} precedents point toward visible, experience-led upgrades, with recurring emphasis on {feature_text}."
    return f"{type_label} precedents point toward visible, experience-led upgrades and clear positioning moves."


def build_takeaways(brief: ProjectBrief, comps: list[dict[str, Any]], strategy: dict[str, Any]) -> list[dict[str, str]]:
    trends: list[str] = []
    feature_counts = _ranked_feature_counts(comps, strategy.get("comparison_matrix_columns", []))
    intervention_counts = _value_counts(comp.get("intervention_type") for comp in comps)
    adaptive_counts = _adaptive_label_counts(comps)

    for label, count in feature_counts[:5]:
        if count >= 2:
            trends.append(f"{label} appears across multiple comps, suggesting it is becoming a baseline part of the competitive set.")
        elif count == 1 and len(comps) <= 3:
            trends.append(f"{label} appears as a distinguishing move within the current comp set.")

    for label, count in intervention_counts[:3]:
        if label and label != "—" and count >= 2:
            trends.append(f"{label} is one of the most common intervention patterns represented in the approved comps.")

    for label, count in adaptive_counts[:4]:
        if count >= 2:
            trends.append(f"{label} is repeatedly used to make project positioning legible to users, tenants, visitors, or the surrounding market.")

    text_pool = " ".join(
        " ".join(
            str(comp.get(key, ""))
            for key in ["project_type", "intervention_type", "key_program", "defining_move", "relevance_to_subject"]
        )
        for comp in comps
    ).lower()
    thematic_trends = [
        ("arrival" in text_pool or "lobby" in text_pool, "Arrival experience is treated as a front-door identity move, not only a circulation or security function."),
        ("amenit" in text_pool or "wellness" in text_pool or "lounge" in text_pool, "Amenity investment is most effective when it supports a broader repositioning story rather than reading as isolated add-on program."),
        ("public" in text_pool or "street" in text_pool or "plaza" in text_pool, "Ground-floor transparency, plaza upgrades, and street-level activation are recurring tools for making large projects feel more accessible."),
        ("retail" in text_pool or "f&b" in text_pool or "restaurant" in text_pool, "Retail and food-and-beverage uses work best when they reinforce the arrival sequence and public interface."),
        ("outdoor" in text_pool or "terrace" in text_pool or "garden" in text_pool, "Outdoor spaces remain a high-value differentiator when they are visible, usable, and connected to the larger amenity strategy."),
        ("transit" in text_pool or "subway" in text_pool or "mobility" in text_pool, "Transit and mobility connections are strongest when they are integrated into the user experience rather than treated as background context."),
        ("premium" in text_pool or "class a" in text_pool or "trophy" in text_pool, "Premium positioning is communicated through a combination of design identity, service quality, and visible shared spaces."),
    ]
    for applies, trend in thematic_trends:
        if applies:
            trends.append(trend)

    fallback_trends = [
        "The strongest comps combine a clear design move with a clear user benefit.",
        "Projects with multiple reinforcing moves tend to read as more complete repositioning efforts than projects with one isolated upgrade.",
        "Market-facing improvements are most convincing when the base, arrival, amenity, and public interface tell the same story.",
        "Successful precedents make their value visible quickly through materials, access, scale, lighting, and program adjacency.",
        "The comp set suggests that early concept work should prioritize a small number of memorable moves over diffuse upgrades.",
    ]
    trends.extend(fallback_trends)

    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for trend in trends:
        short = _short_without_ellipsis(trend, 150)
        key = short.lower()
        if key in seen or "public web sources" in key or "verify" in key:
            continue
        seen.add(key)
        cleaned.append({"trend": short})
        if len(cleaned) >= 10:
            break
    return cleaned[:10]


def _ranked_feature_counts(comps: list[dict[str, Any]], columns: list[str]) -> list[tuple[str, int]]:
    counts = []
    for column in columns:
        count = sum(1 for comp in comps if _comparison_has_value((comp.get("comparison_flags") or {}).get(column)))
        counts.append((column, count))
    return sorted(counts, key=lambda item: (-item[1], columns.index(item[0]) if item[0] in columns else 999))


def _adaptive_label_counts(comps: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for comp in comps:
        for label, value in (comp.get("adaptive_fields") or {}).items():
            if not _blank_adaptive_value(value):
                counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _value_counts(values) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        cleaned = _clean(value)
        if cleaned and not _is_internal_label(cleaned):
            counts[cleaned] = counts.get(cleaned, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _comparison_has_value(value: Any) -> bool:
    return value not in (None, "", "—", "None", False, 0)


def _candidate_sources(attrs: dict[str, Any], record: dict[str, Any], source_log: list[SourceLogEntry]) -> list[dict[str, str]]:
    raw_sources = attrs.get("_sources") if isinstance(attrs.get("_sources"), list) else []
    sources = []
    for source in raw_sources:
        if isinstance(source, dict):
            sources.append(
                {
                    "title": _clean(source.get("name") or source.get("title") or source.get("url")),
                    "publisher": _clean(source.get("name") or source.get("type")),
                    "url": _clean(source.get("url")),
                    "source_type": _clean(source.get("type")) or "public web",
                    "confidence": "medium",
                }
            )
    if sources:
        return sources
    name = str(record.get("project_name", "")).lower()
    for entry in source_log:
        if name and any(part and part in entry.source_name.lower() for part in name.split()[:2]):
            sources.append(
                {
                    "title": entry.source_name,
                    "publisher": entry.source_type,
                    "url": entry.url_or_search,
                    "source_type": entry.source_type,
                    "confidence": "medium",
                }
            )
    return sources


def _dynamic_profile_adaptive_fields(brief: ProjectBrief, records: list[dict[str, Any]], family: str) -> list[str]:
    text = _study_text(brief, records)
    for needles, fields in INTENT_ADAPTIVE_FIELDS:
        if any(needle in text for needle in needles):
            return fields
    fallback = PROFILE_FIELDS_BY_TYPE.get(family, ["Program Strategy", "Public Interface", "Amenity Strategy", "Market Positioning"])
    return _non_redundant_labels(fallback)[:5]


def _cover_intent_label(brief: ProjectBrief, family: str) -> str:
    text = _study_text(brief, [])
    geography = _city_from_location(brief.geography) or brief.geography
    if any(token in text for token in ("landmark", "civic", "museum", "library", "cultural", "public")):
        return "Public Landmarks" + (f" in {geography}" if geography else "")
    if any(token in text for token in ("house", "single family", "villa")):
        return "Residential Context" + (f" in {geography}" if geography else "")
    if family == "residential":
        return "Residential Context" + (f" in {geography}" if geography else "")
    if family == "hotel":
        return "Hospitality Context" + (f" in {geography}" if geography else "")
    if family == "public realm":
        return "Public Realm Context" + (f" in {geography}" if geography else "")
    if family in {"office", "adaptive"} or "office" in text or "workplace" in text or "lobby" in text or "reposition" in text:
        return "Offices" + (f" in {geography}" if geography else "")
    if brief.program_type:
        return _title_label(brief.program_type)
    return "Comparative Context"


def _study_text(brief: ProjectBrief, records: list[dict[str, Any]]) -> str:
    parts: list[str] = [
        brief.project_name,
        brief.address,
        brief.program_type,
        brief.geography,
        " ".join(brief.comp_types),
        " ".join(brief.amenity_priorities),
        " ".join(brief.presentation_priorities),
        str(brief.filters),
    ]
    for record in records:
        parts.extend(
            [
                str(record.get("project_name", "")),
                str(record.get("program_type", "")),
                str(record.get("relevance_summary", "")),
                " ".join(str(note) for note in record.get("review_notes", []) or []),
            ]
        )
    return " ".join(parts).lower()


def _non_redundant_labels(labels: list[str]) -> list[str]:
    universal = {
        "type",
        "scale",
        "year",
        "status",
        "owner",
        "developer",
        "architect",
        "designer",
        "intervention",
        "key program",
        "program type",
    }
    cleaned: list[str] = []
    for label in labels:
        lowered = label.lower()
        if any(term == lowered or term in lowered for term in universal):
            continue
        if label not in cleaned:
            cleaned.append(label)
    return cleaned or labels[:5]


def _adaptive_fields(attrs: dict[str, Any], record: dict[str, Any], strategy: dict[str, Any]) -> dict[str, str]:
    fields = {}
    adaptive = attrs.get("adaptive_fields") if isinstance(attrs.get("adaptive_fields"), dict) else {}
    for label in strategy.get("profile_adaptive_fields", []):
        explicit = _adaptive_value(label, attrs, adaptive, record)
        value = _format_adaptive_value(explicit) if not _blank_adaptive_value(explicit) else ""
        if value and not _duplicates_universal_value(label, value, attrs, record):
            fields[label] = value
    return {key: value for key, value in fields.items() if value and value != "—"}


def _adaptive_value(label: str, attrs: dict[str, Any], adaptive: dict[str, Any], record: dict[str, Any]) -> Any:
    keys = [label, _key(label), label.lower(), *ADAPTIVE_FIELD_ALIASES.get(label, [])]
    for source in (adaptive, attrs, record):
        for key in keys:
            if isinstance(source, dict) and not _blank_adaptive_value(source.get(key)):
                return source.get(key)
    return None


def _blank_adaptive_value(value: Any) -> bool:
    return value in (None, "", "—", "None", [], {}, "not_available", "unknown")


def _format_adaptive_value(value: Any) -> str:
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value if not _blank_adaptive_value(item))
    elif isinstance(value, dict):
        parts = [f"{_title_label(key)}: {val}" for key, val in value.items() if not _blank_adaptive_value(val)]
        text = "; ".join(parts)
    else:
        text = str(value)
    return _short_without_ellipsis(text.strip(), 86)


def _duplicates_universal_value(label: str, value: str, attrs: dict[str, Any], record: dict[str, Any]) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    universal_values = [
        attrs.get("program_type"),
        record.get("program_type"),
        attrs.get("developer_owner"),
        record.get("developer_owner"),
        attrs.get("architect_designer"),
        record.get("architect_designer"),
        attrs.get("completion_year"),
        record.get("completion_year"),
        attrs.get("total_sf"),
        record.get("total_sf"),
    ]
    if any(normalized == str(item).strip().lower() for item in universal_values if item not in (None, "")):
        return True
    return label.lower() in {"scale", "year / status", "owner / developer", "architect / designer", "type", "intervention", "key program"}


def _comparison_flags(columns: list[str], attrs: dict[str, Any], record: dict[str, Any]) -> dict[str, str]:
    text = " ".join([str(attrs), str(record.get("relevance_summary", "")), " ".join(record.get("review_notes") or [])]).lower()
    return {column: _infer_short_value(column, text) for column in columns}


def _dynamic_comparison_columns(default_columns: list[str], comps: list[dict[str, Any]]) -> list[str]:
    scores: dict[str, int] = {}
    ordered: list[str] = []

    def add(label: Any, weight: int = 1) -> None:
        cleaned = _comparison_label(label)
        if not cleaned:
            return
        if cleaned not in scores:
            ordered.append(cleaned)
            scores[cleaned] = 0
        scores[cleaned] += weight

    for comp in comps:
        for label, value in (comp.get("adaptive_fields") or {}).items():
            if not _blank_adaptive_value(value):
                add(label, 5)
        for label, value in (comp.get("comparison_flags") or {}).items():
            if not _blank_adaptive_value(value):
                add(label, 1)
        text = " ".join(
            str(comp.get(key, ""))
            for key in ["project_type", "intervention_type", "key_program", "defining_move", "relevance_to_subject"]
        ).lower()
        for label in default_columns:
            if _feature_in_text(label, text):
                add(label, 1)

    for label in default_columns:
        add(label, 1)

    ranked = sorted(ordered, key=lambda label: (-scores[label], ordered.index(label)))
    return ranked[:12] or default_columns[:8]


def _comparison_flags_from_comp(columns: list[str], comp: dict[str, Any]) -> dict[str, str]:
    fields = comp.get("adaptive_fields") or {}
    text = " ".join(
        [
            str(comp.get("project_type", "")),
            str(comp.get("intervention_type", "")),
            str(comp.get("key_program", "")),
            str(comp.get("defining_move", "")),
            str(comp.get("relevance_to_subject", "")),
            str(fields),
        ]
    ).lower()
    flags: dict[str, str] = {}
    for column in columns:
        if column in fields and not _blank_adaptive_value(fields[column]):
            flags[column] = "●"
        elif _feature_in_text(column, text):
            flags[column] = "●"
        else:
            inferred = _infer_short_value(column, text)
            flags[column] = "●" if inferred != "—" else "—"
    return flags


def _comparison_label(value: Any) -> str:
    label = _clean(value)
    if not label or _is_internal_label(label):
        return ""
    if label.lower() in {"program", "scale"}:
        return ""
    return label


def _feature_in_text(label: str, text: str) -> bool:
    keys = [_key(label), *ADAPTIVE_FIELD_ALIASES.get(label, [])]
    words = [part for key in keys for part in key.split("_") if len(part) >= 4]
    if label.lower() in text:
        return True
    return any(word in text for word in words)


def _infer_short_value(label: str, text: str) -> str:
    column_needles = {
        "Lobby Repositioning": [("lobby", "Lobby"), ("arrival", "Arrival"), ("reposition", "Repositioning")],
        "Tenant Amenities": [("amenit", "Amenity program"), ("lounge", "Lounge"), ("wellness", "Wellness")],
        "F&B": [("f&b", "F&B"), ("food", "F&B"), ("restaurant", "Restaurant / bar"), ("bar", "Restaurant / bar")],
        "Outdoor Space": [("outdoor", "Outdoor space"), ("terrace", "Terrace"), ("garden", "Garden"), ("plaza", "Plaza")],
        "Public Realm": [("public realm", "Public realm"), ("plaza", "Plaza"), ("street", "Street interface")],
        "Transit Connection": [("transit", "Transit-connected"), ("subway", "Transit-connected"), ("path", "Transit-connected")],
        "Retail Activation": [("retail", "Retail activation"), ("ground", "Ground-floor retail")],
        "Program": [("office", "Office"), ("residential", "Residential"), ("hotel", "Hotel"), ("retail", "Retail")],
        "Scale": [("sf", "SF reported"), ("square", "SF reported"), ("units", "Units reported"), ("keys", "Keys reported")],
        "Public Interface": [("public", "Public interface"), ("plaza", "Plaza"), ("street", "Street interface")],
        "Amenities": [("amenit", "Amenity program"), ("wellness", "Wellness")],
        "Adaptive Reuse": [("adaptive", "Adaptive reuse"), ("reuse", "Adaptive reuse"), ("conversion", "Conversion")],
        "Market Positioning": [("premium", "Premium positioning"), ("class a", "Class A"), ("trophy", "Trophy positioning")],
    }
    for needle, value in column_needles.get(label, []):
        if needle in text:
            return value
    return "—"


def _scale(record: dict[str, Any], attrs: dict[str, Any]) -> dict[str, Any]:
    gross_area = _int_or_none(attrs.get("gross_area"))
    rentable_area = _int_or_none(attrs.get("rentable_area") or record.get("total_sf") or attrs.get("total_sf"))
    units = _int_or_none(attrs.get("unit_count") or attrs.get("residential_units"))
    keys = _int_or_none(attrs.get("hotel_keys") or attrs.get("keys"))
    floors = _int_or_none(attrs.get("floors"))
    display = _scale_display(rentable_area or gross_area, units, keys, floors)
    return {
        "display": display,
        "gross_area": gross_area,
        "rentable_area": rentable_area,
        "units": units,
        "keys": keys,
        "floors": floors,
        "height": attrs.get("height"),
        "site_area": attrs.get("site_area"),
    }


def _scale_display(area: int | None, units: int | None, keys: int | None, floors: int | None) -> str:
    if area:
        return f"{area / 1_000_000:.1f}M SF" if area >= 1_000_000 else f"{area:,} SF"
    if units:
        return f"{units:,} units"
    if keys:
        return f"{keys:,} keys"
    if floors:
        return f"{floors} floors"
    return "—"


def _status_year(record: dict[str, Any], attrs: dict[str, Any]) -> str:
    year = _clean(record.get("completion_year") or attrs.get("completion_year") or attrs.get("status_year"))
    status = _clean(record.get("status", "")).replace("_", " ")
    internal_statuses = {"source snapshot", "needs research", "user added", "source enriched"}
    if year and status and status not in internal_statuses:
        return f"{status.title()} · {year}"
    if year:
        return year
    if status in internal_statuses:
        return "—"
    return status.title() if status else "—"


def _project_type(attrs: dict[str, Any], record: dict[str, Any], candidate: CompCandidate | None) -> str:
    for value in [attrs.get("program_type"), record.get("program_type"), candidate.comp_type if candidate else ""]:
        cleaned = _clean(value)
        if cleaned and not _is_internal_label(cleaned):
            return cleaned
    return "—"


def _intervention_type(attrs: dict[str, Any], record: dict[str, Any], candidate: CompCandidate | None, project_type: str) -> str:
    explicit = _clean(
        attrs.get("intervention_type")
        or attrs.get("intervention_strategy")
        or attrs.get("intervention")
        or record.get("intervention_type")
    )
    if explicit and not _is_internal_label(explicit):
        return _title_label(explicit) if explicit == explicit.lower() else explicit

    comp_type = _clean(candidate.comp_type if candidate else "")
    comp_type_for_inference = "" if _is_internal_label(comp_type) else comp_type
    summary = _clean(
        attrs.get("defining_move")
        or attrs.get("relevance_to_subject")
        or attrs.get("presentation_takeaway")
        or record.get("relevance_summary")
    )
    text = " ".join([comp_type_for_inference, project_type, summary]).lower()
    for label, needle in [
        ("Lobby repositioning", "lobby"),
        ("Podium renovation", "podium"),
        ("Base renewal", "base"),
        ("Adaptive reuse", "adaptive"),
        ("Repositioning", "reposition"),
        ("Public realm upgrade", "public realm"),
        ("Mixed-use redevelopment", "mixed"),
        ("Campus expansion", "campus"),
        ("New build", "new"),
        ("Retail activation", "retail"),
    ]:
        if needle in text:
            return label
    return _title_label(comp_type) if comp_type and not _is_internal_label(comp_type) else "—"


def _key_program(project_type: str, attrs: dict[str, Any], record: dict[str, Any]) -> str:
    value = attrs.get("key_program") or project_type or record.get("program_type")
    return _clean(value) or "—"


def _defining_move(record: dict[str, Any], attrs: dict[str, Any]) -> str:
    return _short(_clean(attrs.get("defining_move") or attrs.get("presentation_takeaway") or record.get("relevance_summary")), 110)


def _relevance(record: dict[str, Any], attrs: dict[str, Any]) -> str:
    return _short(_clean(attrs.get("relevance_to_subject") or attrs.get("presentation_takeaway") or record.get("relevance_summary")), 220)


def _selection_reasoning(candidate: CompCandidate | None, record: dict[str, Any]) -> str:
    if candidate and candidate.source_notes:
        return " ".join(candidate.source_notes)
    return _clean(record.get("relevance_summary"))


def _confidence(record: dict[str, Any], sources: list[dict[str, str]]) -> str:
    if len(sources) >= 2 and str(record.get("confidence", "")).lower() in {"high", "medium"}:
        return "high"
    if sources:
        return "medium"
    return str(record.get("confidence") or "low").lower()


def _project_family(text: str) -> str:
    if not text:
        return ""
    for needle in ["life science", "lab", "office", "workplace", "hotel", "residential", "mixed", "institutional", "campus", "public realm", "retail", "adaptive"]:
        if needle in text:
            if needle == "lab":
                return "life science"
            if needle == "workplace":
                return "office"
            return needle
    return ""


def _title_label(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[\s_/.-]+", str(value)) if part)


def _city_from_location(location: str) -> str:
    parts = [part.strip() for part in str(location).split(",") if part.strip()]
    if len(parts) >= 2 and any(char.isdigit() for char in parts[0]):
        return parts[1]
    return parts[0] if parts else ""


def _state_from_location(location: str) -> str:
    parts = [part.strip() for part in str(location).split(",") if part.strip()]
    if len(parts) >= 3 and any(char.isdigit() for char in parts[0]):
        return parts[2]
    return parts[1] if len(parts) > 1 else ""


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _clean(value: Any) -> str:
    if value in (None, "None"):
        return ""
    return str(value).strip()


def _is_internal_label(value: str) -> bool:
    return _key(value) in {
        "user_defined",
        "user_added",
        "user_defined_and_live",
        "live_search",
        "source_snapshot",
        "needs_research",
    }


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value or "—"
    return value[: limit - 1].rsplit(" ", 1)[0] + "…"


def _short_without_ellipsis(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned or "—"
    first_clause = re.split(r"[.;:]", cleaned, maxsplit=1)[0].strip()
    if 24 <= len(first_clause) <= limit:
        return first_clause
    shortened = cleaned[:limit].rsplit(" ", 1)[0].strip(" ,;:")
    return shortened or "—"
