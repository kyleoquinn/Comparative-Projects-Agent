from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

from comp_agent.models import CompCandidate, ProjectBrief, SourceLogEntry, utc_now_iso
from comp_agent.workspace import slugify

logger = logging.getLogger(__name__)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(slots=True)
class LiveSearchResult:
    candidates: list[CompCandidate] = field(default_factory=list)
    source_log: list[SourceLogEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def live_search_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) and os.getenv("COMP_AGENT_LIVE_SEARCH", "").lower() in {"1", "true", "yes"}


class OpenAIWebSearchProvider:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("COMP_AGENT_OPENAI_MODEL", "gpt-5")
        self.timeout_seconds = timeout_seconds or int(os.getenv("COMP_AGENT_OPENAI_TIMEOUT", "120"))

    @classmethod
    def from_env(cls) -> "OpenAIWebSearchProvider | None":
        if not live_search_enabled():
            return None
        return cls(api_key=os.getenv("OPENAI_API_KEY"))

    def discover(self, brief: ProjectBrief, *, max_candidates: int = 5) -> LiveSearchResult:
        if not self.api_key:
            return LiveSearchResult(warnings=["OPENAI_API_KEY is not set; live search skipped."])

        logger.info(f"Starting live search for {brief.project_name}, max_candidates={max_candidates}, timeout={self.timeout_seconds}s")
        
        # Try initial search
        result = self._attempt_search(brief, max_candidates, retry_count=0, use_simpler_query=False)
        
        # If timeout and no candidates, retry once with simpler query
        if not result.candidates and any("timed out" in w.lower() for w in result.warnings):
            logger.warning("Initial search timed out with no candidates, retrying with simpler query")
            result = self._attempt_search(brief, max_candidates, retry_count=1, use_simpler_query=True)
        
        logger.info(f"Search completed: {len(result.candidates)} candidates found, {len(result.warnings)} warnings")
        return result

    def enrich_candidate(self, brief: ProjectBrief, candidate: CompCandidate) -> LiveSearchResult:
        if not self.api_key:
            return LiveSearchResult(warnings=["OPENAI_API_KEY is not set; live enrichment skipped."])

        logger.info(f"Starting live enrichment for approved comp: {candidate.comp_name}")
        start_time = time.time()
        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "user_location": {
                        "type": "approximate",
                        "country": "US",
                        "city": "New York",
                        "region": "New York",
                        "timezone": "America/New_York",
                    },
                }
            ],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "input": self._build_enrichment_input(brief, candidate),
        }

        try:
            response = self._post_json(payload)
        except RuntimeError as error:
            elapsed = time.time() - start_time
            logger.warning(f"Live enrichment failed after {elapsed:.1f}s for {candidate.comp_name}: {error}")
            return LiveSearchResult(warnings=[str(error)])

        text = _response_output_text(response)
        parsed = _parse_json_object(text)
        if not parsed:
            return LiveSearchResult(
                source_log=_source_log_from_response(response),
                warnings=[f"OpenAI web search could not return parseable enrichment JSON for {candidate.comp_name}."],
            )

        candidates = _candidates_from_payload(brief, parsed, max_candidates=1)
        if candidates:
            enriched = candidates[0]
            enriched.comp_id = candidate.comp_id
            enriched.known_attributes = {
                **candidate.known_attributes,
                **{key: value for key, value in enriched.known_attributes.items() if value not in (None, "", [], {})},
            }
            enriched.source_notes = [
                *candidate.source_notes,
                *[note for note in enriched.source_notes if note not in candidate.source_notes],
                "Live web enrichment run after user approval.",
            ]
            if not enriched.location or enriched.location == brief.geography:
                enriched.location = candidate.location
            if not enriched.comp_type or enriched.comp_type == "market benchmark":
                enriched.comp_type = candidate.comp_type
            enriched.status = "source_snapshot"

        source_log = _source_log_from_payload(parsed) or _source_log_from_response(response)
        elapsed = time.time() - start_time
        logger.info(f"Live enrichment completed in {elapsed:.1f}s for {candidate.comp_name}: {len(candidates)} candidate records")
        return LiveSearchResult(candidates=candidates, source_log=source_log, warnings=[str(w) for w in parsed.get("warnings", []) if w])

    def find_image_candidates(self, comp: dict[str, Any], missing_slots: list[str]) -> dict[str, Any]:
        if not self.api_key or not missing_slots:
            return {"image_candidates": [], "warnings": ["Live image repair skipped."]}
        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "user_location": {
                        "type": "approximate",
                        "country": "US",
                        "city": "New York",
                        "region": "New York",
                        "timezone": "America/New_York",
                    },
                }
            ],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "input": self._build_image_repair_input(comp, missing_slots),
        }
        try:
            response = self._post_json(payload)
        except RuntimeError as error:
            return {"image_candidates": [], "warnings": [str(error)]}
        parsed = _parse_json_object(_response_output_text(response))
        if not parsed:
            return {
                "image_candidates": [],
                "sources": [asdict(entry) for entry in _source_log_from_response(response)],
                "warnings": ["OpenAI web search returned unparseable image repair JSON."],
            }
        return parsed

    def repair_candidate(self, brief: ProjectBrief, current_comp: dict[str, Any], missing_fields: list[str]) -> LiveSearchResult:
        if not self.api_key or not missing_fields:
            return LiveSearchResult(warnings=["Live comp repair skipped."])
        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "user_location": {
                        "type": "approximate",
                        "country": "US",
                        "city": "New York",
                        "region": "New York",
                        "timezone": "America/New_York",
                    },
                }
            ],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "input": self._build_candidate_repair_input(brief, current_comp, missing_fields),
        }
        try:
            response = self._post_json(payload)
        except RuntimeError as error:
            return LiveSearchResult(warnings=[str(error)])
        parsed = _parse_json_object(_response_output_text(response))
        if not parsed:
            return LiveSearchResult(
                source_log=_source_log_from_response(response),
                warnings=["OpenAI web search returned unparseable comp repair JSON."],
            )
        candidates = _candidates_from_payload(brief, parsed, max_candidates=1)
        if candidates and current_comp.get("comp_id"):
            candidates[0].comp_id = str(current_comp["comp_id"])
        return LiveSearchResult(
            candidates=candidates,
            source_log=_source_log_from_payload(parsed) or _source_log_from_response(response),
            warnings=[str(w) for w in parsed.get("warnings", []) if w],
        )

    def repair_field(self, comp: dict[str, Any], field_task: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            return {"field": field_task.get("field"), "value": None, "sources": [], "warnings": ["Live field repair skipped."]}
        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "user_location": {
                        "type": "approximate",
                        "country": "US",
                        "city": "New York",
                        "region": "New York",
                        "timezone": "America/New_York",
                    },
                }
            ],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "input": self._build_field_repair_input(comp, field_task),
        }
        try:
            response = self._post_json(payload)
        except RuntimeError as error:
            return {"field": field_task.get("field"), "value": None, "sources": [], "warnings": [str(error)]}
        parsed = _parse_json_object(_response_output_text(response))
        if not parsed:
            return {
                "field": field_task.get("field"),
                "value": None,
                "sources": [asdict(entry) for entry in _source_log_from_response(response)],
                "warnings": ["OpenAI web search returned unparseable field repair JSON."],
            }
        return parsed

    def _attempt_search(self, brief: ProjectBrief, max_candidates: int, retry_count: int, use_simpler_query: bool) -> LiveSearchResult:
        """Attempt a single search with optional simpler query for retries."""
        start_time = time.time()
        logger.info(f"Search attempt {retry_count + 1}, simpler_query={use_simpler_query}")
        
        payload = {
            "model": self.model,
            "reasoning": {"effort": "low"},
            "tools": [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "user_location": {
                        "type": "approximate",
                        "country": "US",
                        "city": "New York",
                        "region": "New York",
                        "timezone": "America/New_York",
                    },
                }
            ],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "input": self._build_input(brief, max_candidates=max_candidates, use_simpler_query=use_simpler_query),
        }
        
        try:
            response = self._post_json(payload)
            elapsed = time.time() - start_time
            logger.info(f"Search attempt {retry_count + 1} succeeded in {elapsed:.1f}s")
        except RuntimeError as error:
            elapsed = time.time() - start_time
            logger.warning(f"Search attempt {retry_count + 1} failed after {elapsed:.1f}s: {error}")
            # Return empty result with warning, not fatal
            return LiveSearchResult(warnings=[str(error)])

        text = _response_output_text(response)
        parsed = _parse_json_object(text)
        
        if not parsed:
            elapsed = time.time() - start_time
            logger.warning(f"Search attempt {retry_count + 1} returned unparseable response after {elapsed:.1f}s")
            # Preserve source log even if JSON parsing failed
            return LiveSearchResult(
                source_log=_source_log_from_response(response),
                warnings=["OpenAI web search returned a response, but it did not contain parseable JSON."],
            )

        candidates = _candidates_from_payload(brief, parsed, max_candidates=max_candidates)
        source_log = _source_log_from_payload(parsed)
        if not source_log:
            source_log = _source_log_from_response(response)
        
        elapsed = time.time() - start_time
        logger.info(f"Search attempt {retry_count + 1} completed in {elapsed:.1f}s with {len(candidates)} candidates")
        
        return LiveSearchResult(candidates=candidates, source_log=source_log)

    def _build_input(self, brief: ProjectBrief, *, max_candidates: int, use_simpler_query: bool = False) -> list[dict[str, str]]:
        if use_simpler_query:
            system = (
                "You are a real estate research assistant. "
                "Use live web search to find comparable projects. "
                "Focus on basic project facts: name, location, comp_type, and one source URL. "
                "Skip detailed image searches and extensive attributes. "
                "Return only JSON. Do not include markdown. Do not invent facts; use null for unknown fields."
            )
            task = f"Find up to {max_candidates} comparable projects for {brief.program_type} in {brief.geography}."
            if brief.comp_guidance:
                task += f" User guidance: {brief.comp_guidance}"
        else:
            system = (
                "You are a real estate research assistant for concept-stage client presentation decks. "
                "Use live web search and prefer official owner/developer, architect, broker, planning, and reputable market sources. "
                "If the user provides excluded_user_defined_comps, do not return those projects as discovered candidates; they will be handled separately. "
                "For initial candidate discovery, include one best source per candidate; deeper source backup happens after approval. "
                "For each candidate, look for multiple direct, usable hero image URLs from official project, owner, architect, or reputable publication pages. "
                "Prioritize three image roles: overall exterior or site identity, relevance focus such as lobby/public realm/amenity/base, and supporting detail or program image. "
                "Prioritize high-quality architectural photography, building facades, lobby/interior spaces, public realm, amenities, and project renderings. "
                "Only include direct image URLs ending in .jpg, .jpeg, .png, or .webp. If no direct image URL is available, set hero_image.image_confidence to not_available. "
                "Include alternative image URLs in hero_image.fallback_urls array if found. "
                "Return only JSON. Do not include markdown. Do not invent facts; use null for unknown fields."
            )
            task = (
                f"Find up to {max_candidates} defensible comparable projects for a client-facing {brief.program_type} "
                "precedent deck. Include the subject building only if useful as context, but candidates "
                "should primarily be comparable precedents. Exclude any projects listed in excluded_user_defined_comps. "
                "Favor projects with public facts that can support a slide."
            )
            if brief.comp_guidance:
                task += f" Use this comp guidance when deciding relevance: {brief.comp_guidance}"
        
        user = {
            "project": {
                "name": brief.project_name,
                "address": brief.address,
                "program_type": brief.program_type,
                "geography": brief.geography,
                "scope_summary": brief.scope_summary,
                "comp_types": brief.comp_types,
                "design_priorities": brief.design_priorities or brief.amenity_priorities,
                "comp_guidance": brief.comp_guidance,
                "radius_miles": brief.radius_miles,
                "time_horizon_years": brief.time_horizon_years,
                "filters": brief.filters,
                "excluded_user_defined_comps": _excluded_user_defined_comps(brief),
            },
            "task": task,
            "required_json_shape": {
                "candidates": [
                    {
                        "project_name": "string",
                        "location": "string",
                        "comp_type": "adaptive reuse | premium workplace | public realm adjacency | market benchmark | other",
                        "relevance_score": "integer 0-100",
                        "status": "source_snapshot",
                        "known_attributes": {
                            "program_type": "string or null",
                            "total_sf": "integer or null",
                            "completion_year": "string or null",
                            "developer_owner": "string or null",
                            "architect_designer": "string or null",
                            "presentation_takeaway": "one sentence or null",
                            "hero_image": {
                                "url": "direct image URL ending .jpg, .jpeg, .png, or .webp if available, otherwise null",
                                "caption": "short image caption or null",
                                "credit": "image/site credit or null",
                                "source_url": "page URL where image was found or null",
                                "image_confidence": "high | medium | low | not_available",
                                "fallback_urls": ["array of alternative image URLs if found"]
                            },
                            "image_package": {
                                "overall": {
                                    "url": "direct image URL for overall exterior/site identity or null",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "confidence": "high | medium | low | not_available"
                                },
                                "focus": {
                                    "url": "direct image URL for lobby/public realm/amenity/base/relevance focus or null",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "confidence": "high | medium | low | not_available"
                                },
                                "detail": {
                                    "url": "direct image URL for supporting detail/program image or null",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "confidence": "high | medium | low | not_available"
                                }
                            },
                            "image_candidates": [
                                {
                                    "url": "direct image URL ending .jpg, .jpeg, .png, or .webp",
                                    "role": "overall | focus | detail | alternate",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "credit": "image/site credit or null",
                                    "confidence": "high | medium | low",
                                    "why_candidate": "brief reason this image may fit the role",
                                }
                            ],
                        },
                        "missing_attributes": ["strings"],
                        "source_notes": ["short sourced notes"],
                        "sources": [
                            {"name": "string", "type": "string", "url": "https URL", "notes": "one best initial source supporting candidate selection"}
                        ],
                    }
                ],
                "warnings": ["strings"],
            },
        }
        return [
            {"role": "developer", "content": system},
            {"role": "user", "content": json.dumps(user, indent=2)},
        ]

    def _build_enrichment_input(self, brief: ProjectBrief, candidate: CompCandidate) -> list[dict[str, str]]:
        system = (
            "You are a real estate research assistant preparing client-facing architectural comp study slides. "
            "Use live web search to enrich exactly one approved comparable project. "
            "Prefer official owner/developer, architect/designer, public agency, consultant, and reputable publication sources. "
            "Find defensible facts and three direct usable image URLs when possible: overall exterior/site identity, relevance focus, and supporting detail. "
            "For a lobby-focused or repositioning comp, the focus image should prioritize lobby/base/public-realm imagery when available. "
            "Only include direct image URLs ending in .jpg, .jpeg, .png, or .webp. "
            "Return only JSON. Do not include markdown. Do not invent facts; use null for unknown fields."
        )
        user = {
            "subject_project": {
                "name": brief.project_name,
                "address": brief.address,
                "program_type": brief.program_type,
                "geography": brief.geography,
                "comp_types": brief.comp_types,
                "amenity_priorities": brief.amenity_priorities,
            },
            "approved_comp_to_enrich": {
                "name": candidate.comp_name,
                "location": candidate.location,
                "comp_type": candidate.comp_type,
                "user_note": candidate.known_attributes.get("user_note") or "",
                "known_attributes": candidate.known_attributes,
                "source_notes": candidate.source_notes,
            },
            "task": (
                "Deep dive this approved comp after user approval. Return one enriched candidate record that can feed a Comp Study Deck profile slide. "
                "Focus on factual project specs, exactly three total primary or strong secondary sources when available, image candidates, and non-redundant adaptive_fields responsive to the study intent. "
                "Preserve the initial discovery source when it is useful, then add two distinct supporting sources during enrichment. "
                "Adaptive fields should explain why the precedent matters for this specific comp study and should not repeat scale, year/status, owner, architect, type, intervention, or key program. "
                "Preserve uncertainty instead of guessing."
            ),
            "adaptive_field_guidance": {
                "instruction": "Populate 3-5 concise client-facing adaptive fields that are responsive to the study intent. Use labels from suggested_labels when they fit; otherwise use similarly concise labels.",
                "suggested_labels": _suggested_adaptive_labels(brief, candidate),
            },
            "required_json_shape": {
                "candidates": [
                    {
                        "project_name": "string",
                        "location": "string",
                        "comp_type": "string",
                        "relevance_score": "integer 0-100",
                        "status": "source_snapshot",
                        "known_attributes": {
                            "program_type": "string or null",
                            "total_sf": "integer or null",
                            "completion_year": "string or null",
                            "developer_owner": "string or null",
                            "architect_designer": "string or null",
                            "intervention_type": "short client-facing strategy label, e.g. Lobby repositioning, Podium renovation, Adaptive reuse, New build, or null",
                            "presentation_takeaway": "one sentence or null",
                            "adaptive_fields": {
                                "Adaptive Field Label": "concise sourced value, non-redundant with universal facts, or null"
                            },
                            "hero_image": {
                                "url": "direct image URL ending .jpg, .jpeg, .png, or .webp if available, otherwise null",
                                "caption": "short image caption or null",
                                "credit": "image/site credit or null",
                                "source_url": "page URL where image was found or null",
                                "image_confidence": "high | medium | low | not_available",
                                "fallback_urls": ["array of alternative image URLs if found"],
                            },
                            "image_package": {
                                "overall": {
                                    "url": "direct image URL for overall exterior/site identity or null",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "confidence": "high | medium | low | not_available",
                                },
                                "focus": {
                                    "url": "direct image URL for lobby/public realm/amenity/base/relevance focus or null",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "confidence": "high | medium | low | not_available",
                                },
                                "detail": {
                                    "url": "direct image URL for supporting detail/program image or null",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "confidence": "high | medium | low | not_available",
                                },
                            },
                            "image_candidates": [
                                {
                                    "url": "direct image URL ending .jpg, .jpeg, .png, or .webp",
                                    "role": "overall | focus | detail | alternate",
                                    "source_url": "page URL where image was found or null",
                                    "caption": "short caption or null",
                                    "credit": "image/site credit or null",
                                    "confidence": "high | medium | low",
                                    "why_candidate": "brief reason this image may fit the role",
                                }
                            ],
                        },
                        "missing_attributes": ["strings"],
                        "source_notes": ["short sourced notes"],
                        "sources": [
                            {"name": "string", "type": "string", "url": "https URL", "notes": "what it supports; return three total sources when available"}
                        ],
                    }
                ],
                "warnings": ["strings"],
            },
        }
        return [
            {"role": "developer", "content": system},
            {"role": "user", "content": json.dumps(user, indent=2)},
        ]

    def _build_image_repair_input(self, comp: dict[str, Any], missing_slots: list[str]) -> list[dict[str, str]]:
        system = (
            "You are helping complete an architectural client presentation deck. "
            "Use live web search to find real project image candidates for only the missing image roles. "
            "Search broadly but efficiently across official project, owner/developer, architect/designer, leasing, consultant, award, and reputable design publication pages. "
            "Return direct image URLs only, not webpage preview thumbnails, logos, icons, maps, or generated placeholders. "
            "Direct image URLs should end in .jpg, .jpeg, .png, or .webp when possible. "
            "For overall, prefer an exterior whole-building or site identity image. "
            "For focus, prefer the study-relevant image such as lobby, entrance, base, amenity, public realm, or street interface. "
            "For detail, prefer a different interior, amenity, facade, plaza, rooftop, or program image. "
            "Return only JSON. Do not include markdown."
        )
        user = {
            "project": {
                "name": comp.get("project_name"),
                "location": comp.get("location"),
                "project_type": comp.get("project_type"),
                "intervention_type": comp.get("intervention_type"),
                "key_program": comp.get("key_program"),
                "relevance_to_subject": comp.get("relevance_to_subject"),
                "primary_sources": comp.get("primary_sources") or [],
            },
            "missing_slots": missing_slots,
            "required_json_shape": {
                "image_candidates": [
                    {
                        "url": "direct image URL",
                        "role": "overall | focus | detail | alternate",
                        "source_url": "page URL where image was found",
                        "caption": "short caption or null",
                        "credit": "image/site credit or null",
                        "confidence": "high | medium | low",
                        "why_candidate": "short reason",
                    }
                ],
                "warnings": ["strings"],
            },
        }
        return [
            {"role": "developer", "content": system},
            {"role": "user", "content": json.dumps(user, indent=2)},
        ]

    def _build_candidate_repair_input(self, brief: ProjectBrief, current_comp: dict[str, Any], missing_fields: list[str]) -> list[dict[str, str]]:
        system = (
            "You are repairing an approved comparable project evidence package for a client-facing architectural deck. "
            "Use live web search, but only target the listed missing or weak fields. "
            "Prefer official owner/developer, architect/designer, public agency, consultant, and reputable publication sources. "
            "Preserve existing facts unless a stronger source directly supports a better value. "
            "Return only JSON. Do not include markdown. Do not invent facts; use null where unresolved."
        )
        user = {
            "subject_project": {
                "name": brief.project_name,
                "address": brief.address,
                "program_type": brief.program_type,
                "geography": brief.geography,
                "comp_types": brief.comp_types,
            },
            "current_comp": current_comp,
            "missing_or_weak_fields": missing_fields,
            "adaptive_field_guidance": {
                "instruction": "If adaptive_fields are missing or weak, fill the requested adaptive labels with concise, client-facing values tied to study intent and not redundant with universal facts.",
                "suggested_labels": current_comp.get("adaptive_field_labels") or current_comp.get("known_attributes", {}).get("adaptive_field_labels") or [],
            },
            "required_json_shape": {
                "candidates": [
                    {
                        "project_name": "string",
                        "location": "string",
                        "comp_type": "string",
                        "relevance_score": "integer 0-100",
                        "status": "source_snapshot",
                        "known_attributes": {
                            "program_type": "string or null",
                            "total_sf": "integer or null",
                            "completion_year": "string or null",
                            "status_year": "string or null",
                            "developer_owner": "string or null",
                            "architect_designer": "string or null",
                            "intervention_type": "short client-facing strategy label, e.g. Lobby repositioning, Podium renovation, Adaptive reuse, New build, or null",
                            "key_program": "string or null",
                            "defining_move": "string or null",
                            "relevance_to_subject": "string or null",
                            "presentation_takeaway": "one sentence or null",
                            "adaptive_fields": {
                                "Adaptive Field Label": "concise sourced value, non-redundant with universal facts, or null"
                            },
                            "hero_image": "object or null",
                            "image_package": "object or null",
                            "image_candidates": ["candidate image objects if found"],
                        },
                        "missing_attributes": ["strings"],
                        "source_notes": ["short sourced notes"],
                        "sources": [
                            {"name": "string", "type": "string", "url": "https URL", "notes": "what it supports; add enough sources to reach three total when available"}
                        ],
                    }
                ],
                "warnings": ["strings"],
            },
        }
        return [
            {"role": "developer", "content": system},
            {"role": "user", "content": json.dumps(user, indent=2)},
        ]

    def _build_field_repair_input(self, comp: dict[str, Any], field_task: dict[str, Any]) -> list[dict[str, str]]:
        system = (
            "You are doing a surgical fact repair for one client-facing deck field. "
            "Use live web search to find only the requested field. "
            "Prefer primary or authoritative sources. Return null if not found. "
            "Return only JSON. Do not include markdown."
        )
        user = {
            "comp": comp,
            "field_task": field_task,
            "required_json_shape": {
                "field": field_task.get("field"),
                "value": "single repaired value or null",
                "display_value": "client-facing display value or null",
                "confidence": "high | medium | low | unresolved",
                "sources": [
                    {"title": "string", "publisher": "string", "url": "https URL", "source_type": "string", "confidence": "high | medium | low"}
                ],
                "image_candidates": [
                    {
                        "url": "direct image URL, only when repairing images",
                        "role": "overall | focus | detail | alternate",
                        "source_url": "page URL where image was found",
                        "caption": "short caption or null",
                        "credit": "image/site credit or null",
                        "confidence": "high | medium | low",
                        "why_candidate": "short reason",
                    }
                ],
                "notes": "short note",
                "warnings": ["strings"],
            },
        }
        return [
            {"role": "developer", "content": system},
            {"role": "user", "content": json.dumps(user, indent=2)},
        ]

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        max_retries = _bounded_int(os.getenv("COMP_AGENT_OPENAI_RETRIES"), default=1)
        last_error: RuntimeError | None = None
        for attempt in range(max_retries + 1):
            request = urllib.request.Request(
                OPENAI_RESPONSES_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"OpenAI live search failed with HTTP {error.code}: {detail}")
                retryable = error.code in {408, 429, 500, 502, 503, 504}
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = RuntimeError(f"OpenAI live search timed out: {error}")
                retryable = True
            if not retryable or attempt >= max_retries:
                break
            time.sleep(min(2.0, 0.75 * (attempt + 1)))
        raise last_error or RuntimeError("OpenAI live search failed.")


def _response_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks: list[str] = []
    for item in response.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _candidates_from_payload(brief: ProjectBrief, payload: dict[str, Any], *, max_candidates: int) -> list[CompCandidate]:
    candidates: list[CompCandidate] = []
    for index, item in enumerate(payload.get("candidates", [])[:max_candidates], start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("project_name") or f"Live search candidate {index}")
        attrs = item.get("known_attributes") if isinstance(item.get("known_attributes"), dict) else {}
        if isinstance(item.get("hero_image"), dict) and "hero_image" not in attrs:
            attrs = {**attrs, "hero_image": item["hero_image"]}
        if isinstance(item.get("image_package"), dict) and "image_package" not in attrs:
            attrs = {**attrs, "image_package": item["image_package"]}
        if isinstance(item.get("sources"), list):
            attrs = {**attrs, "_sources": item["sources"]}
        candidates.append(
            CompCandidate(
                comp_id=slugify(f"{brief.project_name}-{name}"),
                comp_name=name,
                location=str(item.get("location") or brief.geography),
                comp_type=str(item.get("comp_type") or "market benchmark"),
                relevance_score=_bounded_int(item.get("relevance_score"), default=max(55, 90 - index * 5)),
                status="source_snapshot",
                known_attributes=attrs,
                missing_attributes=[str(value) for value in item.get("missing_attributes", []) if value],
                source_notes=[str(value) for value in item.get("source_notes", []) if value],
            )
        )
    return candidates


def _source_log_from_payload(payload: dict[str, Any]) -> list[SourceLogEntry]:
    entries: list[SourceLogEntry] = []
    retrieved_at = utc_now_iso()
    seen: set[str] = set()
    for candidate in payload.get("candidates", []) or []:
        if not isinstance(candidate, dict):
            continue
        for source in candidate.get("sources", []) or []:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "")
            name = str(source.get("name") or url or "Live search source")
            key = url or name
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append(
                SourceLogEntry(
                    source_name=name,
                    source_type=str(source.get("type") or "public web"),
                    url_or_search=url,
                    related_output="openai_live_search",
                    status="retrieved",
                    retrieved_at=retrieved_at,
                    notes=str(source.get("notes") or ""),
                )
            )
    return entries


def _source_log_from_response(response: dict[str, Any]) -> list[SourceLogEntry]:
    entries: list[SourceLogEntry] = []
    retrieved_at = utc_now_iso()
    seen: set[str] = set()
    for item in response.get("output", []) or []:
        if item.get("type") != "web_search_call":
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        for source in action.get("sources", []) or []:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "")
            title = str(source.get("title") or url or "OpenAI web search source")
            if not url or url in seen:
                continue
            seen.add(url)
            entries.append(
                SourceLogEntry(
                    source_name=title,
                    source_type="openai web_search source",
                    url_or_search=url,
                    related_output="openai_live_search",
                    status="retrieved",
                    retrieved_at=retrieved_at,
                    notes="Consulted by OpenAI web_search.",
                )
            )
    return entries


def _excluded_user_defined_comps(brief: ProjectBrief) -> list[dict[str, str]]:
    excluded = brief.filters.get("excluded_user_defined_comps")
    if not isinstance(excluded, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in excluded:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "location": str(item.get("location") or "").strip(),
                "note": str(item.get("note") or "").strip(),
            }
        )
    return normalized


def _suggested_adaptive_labels(brief: ProjectBrief, candidate: CompCandidate) -> list[str]:
    text = " ".join(
        [
            brief.project_name,
            brief.address,
            brief.program_type,
            " ".join(brief.comp_types),
            " ".join(brief.amenity_priorities),
            " ".join(brief.presentation_priorities),
            candidate.comp_name,
            candidate.comp_type,
            " ".join(candidate.source_notes),
            str(candidate.known_attributes),
        ]
    ).lower()
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


def _bounded_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, number))
