"""Wide-funnel discovery orchestration.

Replaces the previous single-call discovery with a planner -> fan-out ->
dedupe -> top-up pipeline so that briefs requesting many candidates (up to
``MAX_TARGET_COUNT``) receive a diverse, deduplicated slate that reliably
hits the requested count when the universe of comps allows.

Phase 1 scope: discovery + replacement helpers used by the approval-stage
swap UI. Post-deck replacement is deferred.

Stability defaults (per saved feedback ``feedback_stability_over_speed``):

* ``DEFAULT_FANOUT_CONCURRENCY = 3`` — small enough to avoid OpenAI
  rate-limit exposure even under bursty use.
* Single top-up round only; underdelivery surfaces a warning rather than
  retrying indefinitely.
* Every fan-out call already retries once with a simplified query inside
  :class:`OpenAIWebSearchProvider` itself, so a transient timeout per axis
  does not kill the run.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from comp_agent.models import CompCandidate, ProjectBrief, SourceLogEntry
from comp_agent.openai_search import LiveSearchResult, OpenAIWebSearchProvider

logger = logging.getLogger(__name__)


DEFAULT_TARGET_COUNT = 10
MAX_TARGET_COUNT = 50
DEFAULT_FANOUT_CONCURRENCY = 3


@dataclass(slots=True)
class DiscoveryResult:
    """Outcome of a wide-funnel discovery run.

    Mirrors :class:`LiveSearchResult` for the fields downstream callers
    expect, plus extra metadata about how many rounds and axes were used so
    the UI can be honest about underdelivery.
    """

    candidates: list[CompCandidate] = field(default_factory=list)
    source_log: list[SourceLogEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    axes_used: list[dict[str, Any]] = field(default_factory=list)
    target_count: int = 0
    delivered_count: int = 0
    rounds_used: int = 0


def discover_with_target(
    provider: OpenAIWebSearchProvider,
    brief: ProjectBrief,
    target_count: int,
    *,
    concurrency: int | None = None,
    comps_per_scope: dict[str, int] | None = None,
) -> DiscoveryResult:
    """Run the full wide-funnel discovery pipeline.

    1. Plan: ask the model for diverse search axes (with a single-axis
       fallback if the planner is unavailable).
    2. Round 1 fan-out: parallel axis-specific discovery calls (cap at
       ``concurrency``).
    3. Dedupe: string normalization first, LLM tiebreak on near-misses.
    4. Top-up (one round only): if still short, run a small fan-out with
       the existing comp set as exclusion list.
    5. Truncate to ``target_count`` and surface warnings on underdelivery.

    When ``comps_per_scope`` is provided (e.g. ``{"local": 5, "national": 3}``),
    the planner is invoked once per scope so each axis carries an explicit
    geographic guardrail and the requested per-scope counts are honored.
    """
    scope_targets = _normalize_scope_targets(comps_per_scope or brief.comps_per_scope)
    target = sum(scope_targets.values()) if scope_targets else _bounded_target(target_count)
    concurrency = _bounded_concurrency(concurrency)
    logger.info(
        f"Starting discovery for {brief.project_name}, target={target}, "
        f"scopes={scope_targets or 'none'}, concurrency={concurrency}"
    )

    warnings: list[str] = []
    if scope_targets:
        axes: list[dict[str, Any]] = []
        for scope, scope_target in scope_targets.items():
            scope_plan = provider.plan_axes(brief, scope_target, scope=scope)
            warnings.extend(scope_plan.get("warnings") or [])
            for axis in scope_plan.get("axes") or []:
                axes.append({**axis, "scope": scope})
    else:
        plan = provider.plan_axes(brief, target)
        axes = list(plan.get("axes") or [])
        warnings.extend(plan.get("warnings") or [])

    if not axes:
        return DiscoveryResult(
            warnings=[*warnings, "Planner returned no axes; discovery aborted."],
            target_count=target,
            rounds_used=0,
        )

    # Round 1: parallel fan-out.
    raw_candidates, source_log, fanout_warnings = _run_fanout(
        provider, brief, axes, exclude=[], steer=None, concurrency=concurrency
    )
    warnings.extend(fanout_warnings)

    # Dedupe round 1.
    deduped, dedupe_warnings = _dedupe(provider, raw_candidates)
    warnings.extend(dedupe_warnings)
    rounds_used = 1

    # Round 2 (top-up) only if needed.
    if len(deduped) < target:
        gap = target - len(deduped)
        logger.info(f"Round 1 returned {len(deduped)} unique; firing top-up for {gap} more.")
        topup_axes = _scale_axes_for_topup(axes, gap)
        exclusion = _build_exclusion_list(deduped)
        topup_raw, topup_log, topup_warnings = _run_fanout(
            provider, brief, topup_axes, exclude=exclusion, steer=None, concurrency=concurrency
        )
        warnings.extend(topup_warnings)
        source_log.extend(topup_log)
        if topup_raw:
            combined = list(deduped) + list(topup_raw)
            deduped, dedupe_warnings = _dedupe(provider, combined)
            warnings.extend(dedupe_warnings)
        rounds_used = 2

    final = deduped[:target]
    if len(final) < target:
        warnings.append(
            f"Asked for {target} comps; could only deliver {len(final)} unique. "
            "Refine the brief or accept the smaller set."
        )

    logger.info(f"Discovery complete: {len(final)} of {target} comps, rounds={rounds_used}")
    return DiscoveryResult(
        candidates=final,
        source_log=source_log,
        warnings=warnings,
        axes_used=list(axes),
        target_count=target,
        delivered_count=len(final),
        rounds_used=rounds_used,
    )


def replace_candidates(
    provider: OpenAIWebSearchProvider,
    brief: ProjectBrief,
    axes: list[dict[str, Any]],
    existing: list[CompCandidate],
    exclude_ids: list[str],
    count_needed: int,
    *,
    steer: str | None = None,
    concurrency: int | None = None,
) -> DiscoveryResult:
    """Find replacement candidates for the approval-stage swap UI.

    Builds the exclusion list from *all* current candidates (so we never
    return duplicates of what the user already has) plus any IDs they
    explicitly rejected. Reuses the planner's original axes rather than
    replanning, and applies optional ``steer`` text as a free-form bias.

    Used by Phase 2's ``/api/discover/replace`` endpoint.
    """
    if count_needed <= 0:
        return DiscoveryResult(
            warnings=["No replacements requested (count_needed <= 0)."],
            target_count=0,
            rounds_used=0,
        )
    concurrency = _bounded_concurrency(concurrency)
    exclusion_set = set(str(value) for value in (exclude_ids or []))
    exclusion = _build_exclusion_list(existing, also_include_rejected_ids=exclusion_set, all_existing=True)
    sized_axes = _scale_axes_for_topup(axes, count_needed)
    raw_candidates, source_log, warnings = _run_fanout(
        provider, brief, sized_axes, exclude=exclusion, steer=steer, concurrency=concurrency
    )
    if not raw_candidates:
        return DiscoveryResult(
            source_log=source_log,
            warnings=[*warnings, "No replacement candidates returned."],
            axes_used=list(axes),
            target_count=count_needed,
            rounds_used=1,
        )
    # Dedupe new candidates against existing + each other.
    combined = list(existing) + list(raw_candidates)
    deduped, dedupe_warnings = _dedupe(provider, combined)
    # Drop the existing slate; only return fresh ones.
    existing_ids = {c.comp_id for c in existing}
    fresh = [c for c in deduped if c.comp_id not in existing_ids][:count_needed]
    return DiscoveryResult(
        candidates=fresh,
        source_log=source_log,
        warnings=[*warnings, *dedupe_warnings],
        axes_used=list(axes),
        target_count=count_needed,
        delivered_count=len(fresh),
        rounds_used=1,
    )


def _run_fanout(
    provider: OpenAIWebSearchProvider,
    brief: ProjectBrief,
    axes: list[dict[str, Any]],
    *,
    exclude: list[dict[str, str]],
    steer: str | None,
    concurrency: int,
) -> tuple[list[CompCandidate], list[SourceLogEntry], list[str]]:
    """Execute axis discovery calls with bounded parallelism.

    Each ``provider.discover_axis`` call already retries internally on
    timeout, so this layer just collects results and warnings; one failed
    axis does not abort the run.
    """
    candidates: list[CompCandidate] = []
    source_log: list[SourceLogEntry] = []
    warnings: list[str] = []
    if not axes:
        return candidates, source_log, warnings
    if concurrency <= 1 or len(axes) <= 1:
        for axis in axes:
            result = _safe_discover_axis(provider, brief, axis, exclude, steer)
            candidates.extend(result.candidates)
            source_log.extend(result.source_log)
            warnings.extend(result.warnings)
        return candidates, source_log, warnings
    max_workers = min(concurrency, len(axes))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_safe_discover_axis, provider, brief, axis, exclude, steer): axis
            for axis in axes
        }
        for future in as_completed(future_map):
            result = future.result()
            candidates.extend(result.candidates)
            source_log.extend(result.source_log)
            warnings.extend(result.warnings)
    return candidates, source_log, warnings


def _safe_discover_axis(
    provider: OpenAIWebSearchProvider,
    brief: ProjectBrief,
    axis: dict[str, Any],
    exclude: list[dict[str, str]],
    steer: str | None,
) -> LiveSearchResult:
    """Wrap ``discover_axis`` so an unexpected exception does not abort the fan-out."""
    try:
        return provider.discover_axis(brief, axis, exclude=exclude, steer=steer)
    except Exception as error:  # noqa: BLE001 - defensive boundary
        logger.warning(f"Axis '{axis.get('label')}' raised {type(error).__name__}: {error}")
        return LiveSearchResult(warnings=[f"Axis '{axis.get('label')}' failed: {error}"])


def _dedupe(
    provider: OpenAIWebSearchProvider,
    candidates: list[CompCandidate],
) -> tuple[list[CompCandidate], list[str]]:
    """Two-pass dedupe: normalize-and-merge exact matches, then LLM-check near-misses.

    The string pass handles formatting variance for free; the LLM pass
    catches building aliases (e.g. ``200 Vesey`` ≡ ``Brookfield Place North
    Tower``) that string matching cannot. The LLM pass falls back to
    treating ambiguous pairs as distinct on any failure (favors over-
    delivery, surfaces a warning).
    """
    if not candidates:
        return [], []
    string_unique, near_misses = _string_dedupe(candidates)
    warnings: list[str] = []
    if not near_misses:
        return string_unique, warnings
    payload = [
        {
            "pair_id": str(index),
            "left": {"name": pair[0].comp_name, "location": pair[0].location},
            "right": {"name": pair[1].comp_name, "location": pair[1].location},
        }
        for index, pair in enumerate(near_misses)
    ]
    decision_response = provider.dedupe_check(payload)
    warnings.extend(decision_response.get("warnings") or [])
    decisions_by_pair = {str(d.get("pair_id")): d for d in decision_response.get("decisions") or []}
    drop_ids: set[str] = set()
    for index, pair in enumerate(near_misses):
        decision = decisions_by_pair.get(str(index)) or {}
        if not decision.get("same_project"):
            continue
        # Same project: drop the lower-relevance side; keep the higher-
        # relevance one as canonical (regardless of canonical_choice from
        # the model — we trust local relevance scores to break ties).
        left, right = pair
        if right.relevance_score > left.relevance_score:
            drop_ids.add(left.comp_id)
        else:
            drop_ids.add(right.comp_id)
    if not drop_ids:
        return string_unique, warnings
    return [c for c in string_unique if c.comp_id not in drop_ids], warnings


DEDUPE_NEAR_MISS_CAP = 30


def _string_dedupe(
    candidates: list[CompCandidate],
) -> tuple[list[CompCandidate], list[tuple[CompCandidate, CompCandidate]]]:
    """Drop exact-normalized-name duplicates and surface near-miss pairs for LLM tiebreak.

    Returns ``(unique_candidates, near_miss_pairs)``. ``unique_candidates``
    preserves the highest-relevance entry on collision.

    The near-miss heuristic flags **every same-city pair of distinct
    candidates** so the LLM can catch the alias case the user explicitly
    cares about (e.g. ``200 Vesey Street`` ≡ ``Brookfield Place North
    Tower`` — same building, no shared name prefix). This widens cost vs.
    a prefix-only heuristic, but cost stays bounded:

    * The pair count is capped at ``DEDUPE_NEAR_MISS_CAP`` per call
      (priority given to higher-relevance pairs first).
    * The LLM dedupe prompt biases toward ``same_project=false`` when
      unsure, so genuinely different buildings in the same city are
      preserved.

    Pairs across different cities are never flagged: physically different
    locations cannot be the same building.
    """
    by_norm: dict[str, CompCandidate] = {}
    for candidate in candidates:
        key = _normalize_for_dedupe(candidate.comp_name, candidate.location)
        existing = by_norm.get(key)
        if existing is None or candidate.relevance_score > existing.relevance_score:
            by_norm[key] = candidate
    unique = list(by_norm.values())

    # Group by normalized city and flag every distinct pair within a city.
    by_city: dict[str, list[CompCandidate]] = {}
    for candidate in unique:
        city = _normalize_city(candidate.location)
        if not city:
            continue
        by_city.setdefault(city, []).append(candidate)

    near_misses: list[tuple[CompCandidate, CompCandidate]] = []
    seen_pairs: set[tuple[str, str]] = set()
    # Prioritize pairs by combined relevance so the cap (if hit) still
    # examines the most-likely-real candidates first.
    for city_candidates in by_city.values():
        if len(city_candidates) < 2:
            continue
        sorted_candidates = sorted(city_candidates, key=lambda c: c.relevance_score, reverse=True)
        for index, left in enumerate(sorted_candidates):
            left_norm = _normalize_name(left.comp_name)
            for right in sorted_candidates[index + 1 :]:
                if _normalize_name(right.comp_name) == left_norm:
                    continue  # would have been merged in the exact-match pass
                pair_key = tuple(sorted([left.comp_id, right.comp_id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                near_misses.append((left, right))
                if len(near_misses) >= DEDUPE_NEAR_MISS_CAP:
                    return unique, near_misses
    return unique, near_misses


def _normalize_for_dedupe(name: str, location: str) -> str:
    return f"{_normalize_name(name)}|{_normalize_city(location)}"


_NAME_STRIP_PREFIXES = ("the ",)
_NAME_STRIP_SUFFIXES = (
    " building",
    " tower",
    " place",
)
_STREET_NORMALIZATIONS = [
    (re.compile(r"\bstreet\b", re.IGNORECASE), "st"),
    (re.compile(r"\bavenue\b", re.IGNORECASE), "ave"),
    (re.compile(r"\bboulevard\b", re.IGNORECASE), "blvd"),
    (re.compile(r"\broad\b", re.IGNORECASE), "rd"),
    (re.compile(r"\bdrive\b", re.IGNORECASE), "dr"),
]


def _normalize_name(name: str) -> str:
    text = (name or "").strip().lower()
    for prefix in _NAME_STRIP_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    for pattern, replacement in _STREET_NORMALIZATIONS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in _NAME_STRIP_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def _normalize_city(location: str) -> str:
    text = (location or "").strip().lower()
    if not text:
        return ""
    # Take first segment (before comma) — usually city.
    head = text.split(",", 1)[0]
    head = re.sub(r"[^a-z0-9 ]+", "", head)
    return re.sub(r"\s+", " ", head).strip()


def _build_exclusion_list(
    candidates: list[CompCandidate],
    *,
    also_include_rejected_ids: set[str] | None = None,
    all_existing: bool = False,
) -> list[dict[str, str]]:
    """Build the ``must_not_include`` payload for the next discovery call.

    By default we exclude every accepted candidate (so top-up calls don't
    return duplicates of round 1). If ``also_include_rejected_ids`` is
    provided, candidates with those ids are also excluded — used by the
    replacement flow to make sure rejected comps don't come back. If
    ``all_existing`` is True we include every candidate regardless of id
    membership (replacement flow exclusion list).
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    rejected = also_include_rejected_ids or set()
    for candidate in candidates:
        if not all_existing and candidate.comp_id in rejected:
            pass  # still add — we want to exclude these
        key = f"{candidate.comp_name}|{candidate.location}".lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": candidate.comp_name, "location": candidate.location})
    return out


def _scale_axes_for_topup(axes: list[dict[str, Any]], gap: int) -> list[dict[str, Any]]:
    """Reuse the original axes for top-up but resize ``target_count`` to fit the gap.

    Heuristic: split the gap evenly across axes (minimum 2 per axis) and
    cap the number of axes at ``min(len(axes), ceil(gap/2))`` so we don't
    fan out wider than necessary. This keeps top-up costs bounded while
    still preserving diversity.
    """
    if gap <= 0 or not axes:
        return []
    needed_axes = max(1, min(len(axes), (gap + 1) // 2))
    selected = list(axes[:needed_axes])
    per_axis = max(2, (gap + needed_axes - 1) // needed_axes)
    return [
        {**axis, "target_count": per_axis}
        for axis in selected
    ]


def _normalize_scope_targets(value: Any) -> dict[str, int]:
    """Coerce ``comps_per_scope`` input into an ordered ``{scope: count}`` dict.

    Drops scopes with zero/missing counts and clamps the total against
    ``MAX_TARGET_COUNT``. Order is local -> national -> international so
    fan-out reads naturally in logs.
    """
    if not isinstance(value, dict):
        return {}
    valid_order = ("local", "national", "international")
    normalized: dict[str, int] = {}
    for scope in valid_order:
        raw = value.get(scope)
        try:
            count = int(raw)
        except (TypeError, ValueError):
            continue
        if count > 0:
            normalized[scope] = count
    if not normalized:
        return {}
    total = sum(normalized.values())
    if total > MAX_TARGET_COUNT:
        scale = MAX_TARGET_COUNT / total
        scaled = {scope: max(1, int(count * scale)) for scope, count in normalized.items()}
        # Adjust any rounding drift to land exactly on the cap.
        drift = MAX_TARGET_COUNT - sum(scaled.values())
        if drift:
            first_scope = next(iter(scaled))
            scaled[first_scope] = max(1, scaled[first_scope] + drift)
        normalized = scaled
    return normalized


def _bounded_target(target_count: int) -> int:
    try:
        value = int(target_count)
    except (TypeError, ValueError):
        return DEFAULT_TARGET_COUNT
    return max(1, min(MAX_TARGET_COUNT, value))


def _bounded_concurrency(concurrency: int | None) -> int:
    if concurrency is not None:
        try:
            return max(1, int(concurrency))
        except (TypeError, ValueError):
            return DEFAULT_FANOUT_CONCURRENCY
    raw = os.getenv("COMP_AGENT_FANOUT_CONCURRENCY")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_FANOUT_CONCURRENCY
