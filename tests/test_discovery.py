"""Tests for the wide-funnel discovery orchestration.

These tests use a hand-rolled fake provider so they run offline (no
``OPENAI_API_KEY`` required, no network). Each test isolates one piece of
the pipeline so a regression in dedupe doesn't masquerade as a regression
in top-up logic.
"""

from __future__ import annotations

from typing import Any

from comp_agent.discovery import (
    DEFAULT_TARGET_COUNT,
    MAX_TARGET_COUNT,
    DiscoveryResult,
    _build_exclusion_list,
    _normalize_for_dedupe,
    _normalize_name,
    _scale_axes_for_topup,
    _string_dedupe,
    discover_with_target,
    replace_candidates,
)
from comp_agent.models import CompCandidate, ProjectBrief
from comp_agent.openai_search import LiveSearchResult


def _candidate(comp_id: str, name: str, location: str, score: int = 70) -> CompCandidate:
    return CompCandidate(
        comp_id=comp_id,
        comp_name=name,
        location=location,
        comp_type="market benchmark",
        relevance_score=score,
        status="source_snapshot",
    )


def _brief() -> ProjectBrief:
    return ProjectBrief(
        project_name="Test Brief",
        address="100 Main St, Example City",
        program_type="office repositioning",
        geography="Example City",
        comp_types=["adaptive reuse", "premium workplace"],
    )


class FakeProvider:
    """Minimal stand-in for ``OpenAIWebSearchProvider`` used by tests.

    Exposes only the surface ``discovery`` actually calls: ``plan_axes``,
    ``discover_axis``, and ``dedupe_check``. Each method records the calls
    it received so tests can assert on the orchestration behavior.
    """

    def __init__(
        self,
        *,
        axes: list[dict[str, Any]] | None = None,
        per_axis_candidates: dict[str, list[CompCandidate]] | None = None,
        dedupe_decisions: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._axes = axes or [
            {"label": "axis-a", "prompt_fragment": "fragment a", "target_count": 5},
            {"label": "axis-b", "prompt_fragment": "fragment b", "target_count": 5},
        ]
        self._per_axis = per_axis_candidates or {}
        self._dedupe = dedupe_decisions or {}
        self.plan_calls: list[int] = []
        self.axis_calls: list[dict[str, Any]] = []
        self.dedupe_calls: list[list[dict[str, Any]]] = []

    def plan_axes(self, brief: ProjectBrief, target_count: int) -> dict[str, Any]:
        self.plan_calls.append(target_count)
        return {"axes": list(self._axes), "warnings": []}

    def discover_axis(
        self,
        brief: ProjectBrief,
        axis: dict[str, Any],
        *,
        exclude: list[dict[str, str]] | None = None,
        steer: str | None = None,
    ) -> LiveSearchResult:
        label = str(axis.get("label"))
        self.axis_calls.append(
            {"label": label, "exclude": list(exclude or []), "steer": steer}
        )
        candidates = list(self._per_axis.get(label, []))
        return LiveSearchResult(candidates=candidates)

    def dedupe_check(self, near_misses: list[dict[str, Any]]) -> dict[str, Any]:
        self.dedupe_calls.append(list(near_misses))
        decisions = []
        for pair in near_misses:
            pair_id = str(pair.get("pair_id"))
            decisions.append(
                self._dedupe.get(
                    pair_id,
                    {"pair_id": pair_id, "same_project": False, "canonical_choice": None},
                )
            )
        return {"decisions": decisions, "warnings": []}


# ---------------------------------------------------------------------------
# Pure-logic dedupe tests (no provider needed)
# ---------------------------------------------------------------------------


def test_normalize_handles_street_variance():
    # "Street" vs "St" should normalize to the same form.
    assert _normalize_name("200 Vesey Street") == _normalize_name("200 Vesey St")


def test_normalize_strips_leading_the_and_punctuation():
    assert _normalize_name("The Spiral") == _normalize_name("Spiral")
    assert _normalize_name("Hudson Yards") == _normalize_name("Hudson, Yards!")


def test_string_dedupe_merges_address_variance():
    # Two address-variant entries should collapse to one (highest relevance wins).
    candidates = [
        _candidate("a", "200 Vesey St", "New York, NY", score=72),
        _candidate("b", "200 Vesey Street", "New York, NY", score=80),
    ]
    unique, near_misses = _string_dedupe(candidates)
    assert len(unique) == 1
    # Highest relevance (80) survives the merge.
    assert unique[0].comp_id == "b"
    # Exact normalized match -> not a near-miss; LLM call shouldn't fire.
    assert near_misses == []


def test_string_dedupe_keeps_distinct_addresses():
    candidates = [
        _candidate("a", "100 Main St", "Boston, MA"),
        _candidate("b", "200 Main St", "Boston, MA"),
    ]
    unique, _ = _string_dedupe(candidates)
    assert len(unique) == 2


def test_string_dedupe_flags_near_misses_for_llm():
    # Same city + name prefix overlap (>=4 chars) but different normalized name
    # -> flagged for LLM tiebreak.
    candidates = [
        _candidate("a", "Brookfield Place North Tower", "New York, NY"),
        _candidate("b", "Brookfield Plaza", "New York, NY"),
    ]
    _, near_misses = _string_dedupe(candidates)
    assert len(near_misses) == 1


def test_string_dedupe_does_not_flag_across_cities():
    # Different cities -> not a near-miss even if names share a prefix.
    candidates = [
        _candidate("a", "Hudson Yards", "New York, NY"),
        _candidate("b", "Hudson Tower", "Boston, MA"),
    ]
    _, near_misses = _string_dedupe(candidates)
    assert near_misses == []


def test_normalize_for_dedupe_combines_name_and_city():
    # Sanity check on the composite key used as the dedupe map key.
    assert _normalize_for_dedupe("200 Vesey Street", "New York, NY") == _normalize_for_dedupe(
        "200 Vesey St", "New York, USA -- New York"
    )


# ---------------------------------------------------------------------------
# Helper-function tests
# ---------------------------------------------------------------------------


def test_scale_axes_for_topup_caps_axis_count_at_gap():
    # Don't fire all axes for a tiny gap.
    axes = [
        {"label": "a", "prompt_fragment": "x", "target_count": 5},
        {"label": "b", "prompt_fragment": "x", "target_count": 5},
        {"label": "c", "prompt_fragment": "x", "target_count": 5},
    ]
    sized = _scale_axes_for_topup(axes, gap=2)
    # gap=2 -> max 1 axis with target_count >= 2
    assert len(sized) == 1
    assert sized[0]["target_count"] >= 2


def test_scale_axes_for_topup_handles_zero_gap():
    axes = [{"label": "a", "prompt_fragment": "x", "target_count": 5}]
    assert _scale_axes_for_topup(axes, gap=0) == []


def test_build_exclusion_list_dedupes_by_name_and_location():
    candidates = [
        _candidate("a", "200 Vesey Street", "New York, NY"),
        _candidate("b", "200 Vesey Street", "New York, NY"),  # dup at the string level
        _candidate("c", "Hudson Yards", "New York, NY"),
    ]
    exclusion = _build_exclusion_list(candidates)
    assert len(exclusion) == 2  # "200 Vesey Street" deduped
    names = {item["name"] for item in exclusion}
    assert names == {"200 Vesey Street", "Hudson Yards"}


# ---------------------------------------------------------------------------
# Orchestration tests (FakeProvider; concurrency=1 for determinism)
# ---------------------------------------------------------------------------


def test_discover_with_target_returns_deduped_slate():
    # Two axes, each returns 3 candidates, no overlap -> 6 unique.
    axes = [
        {"label": "axis-a", "prompt_fragment": "x", "target_count": 3},
        {"label": "axis-b", "prompt_fragment": "y", "target_count": 3},
    ]
    provider = FakeProvider(
        axes=axes,
        per_axis_candidates={
            "axis-a": [_candidate(f"a{i}", f"Project A{i}", "City A") for i in range(3)],
            "axis-b": [_candidate(f"b{i}", f"Project B{i}", "City B") for i in range(3)],
        },
    )
    result = discover_with_target(provider, _brief(), target_count=6, concurrency=1)
    assert isinstance(result, DiscoveryResult)
    assert result.delivered_count == 6
    assert result.rounds_used == 1  # round 1 hit the target, no top-up
    assert len(provider.axis_calls) == 2  # one per axis, no top-up
    assert provider.plan_calls == [6]


def test_discover_with_target_truncates_to_target_count():
    # Round 1 over-delivers; result should be trimmed to target.
    axes = [{"label": "axis-a", "prompt_fragment": "x", "target_count": 10}]
    provider = FakeProvider(
        axes=axes,
        per_axis_candidates={
            "axis-a": [_candidate(f"a{i}", f"Project A{i}", "City A") for i in range(10)],
        },
    )
    result = discover_with_target(provider, _brief(), target_count=5, concurrency=1)
    assert result.delivered_count == 5
    assert result.rounds_used == 1


def test_discover_with_target_fires_topup_when_short():
    # Round 1 under target -> top-up fires with exclusion list of round 1.
    axes = [{"label": "axis-a", "prompt_fragment": "x", "target_count": 5}]
    provider = FakeProvider(
        axes=axes,
        per_axis_candidates={
            "axis-a": [
                _candidate("a0", "Project A0", "City A"),
                _candidate("a1", "Project A1", "City A"),
            ],
        },
    )
    # Reconfigure axis-a to return more candidates on the *second* call (top-up).
    # The patched function still records the call into ``provider.axis_calls``
    # so the test can assert on what the top-up received.
    second_call_candidates = [_candidate(f"new{i}", f"New {i}", "City A") for i in range(3)]
    real_discover = provider.discover_axis
    call_count = {"n": 0}

    def staged_discover(brief, axis, *, exclude=None, steer=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_discover(brief, axis, exclude=exclude, steer=steer)
        provider.axis_calls.append(
            {"label": str(axis.get("label")), "exclude": list(exclude or []), "steer": steer}
        )
        return LiveSearchResult(candidates=second_call_candidates)

    provider.discover_axis = staged_discover  # type: ignore[assignment]

    result = discover_with_target(provider, _brief(), target_count=5, concurrency=1)
    assert result.rounds_used == 2
    assert result.delivered_count == 5
    # Top-up call should have received exclusion list with round-1 names.
    topup_call = provider.axis_calls[1]
    excluded_names = {item["name"] for item in topup_call["exclude"]}
    assert "Project A0" in excluded_names
    assert "Project A1" in excluded_names


def test_discover_with_target_warns_on_underdelivery():
    # Both rounds short of target -> warning surfaced.
    axes = [{"label": "axis-a", "prompt_fragment": "x", "target_count": 5}]
    provider = FakeProvider(
        axes=axes,
        per_axis_candidates={
            "axis-a": [_candidate("a0", "Project A0", "City A")],
        },
    )
    result = discover_with_target(provider, _brief(), target_count=10, concurrency=1)
    assert result.delivered_count < 10
    assert any("Asked for 10" in w for w in result.warnings)


def test_discover_with_target_clamps_target_to_max():
    # Caller passes 999; should clamp to MAX_TARGET_COUNT in the planner call.
    provider = FakeProvider(per_axis_candidates={})
    discover_with_target(provider, _brief(), target_count=999, concurrency=1)
    assert provider.plan_calls == [MAX_TARGET_COUNT]


def test_discover_with_target_uses_default_when_target_invalid():
    provider = FakeProvider(per_axis_candidates={})
    discover_with_target(provider, _brief(), target_count=0, concurrency=1)
    # 0 is below the minimum (1); _bounded_target floors at 1.
    assert provider.plan_calls == [1]


def test_llm_dedupe_drops_lower_relevance_when_same_project():
    # Near-miss where the LLM says "same project" -> the lower-relevance
    # entry is dropped.
    axes = [{"label": "axis-a", "prompt_fragment": "x", "target_count": 2}]
    provider = FakeProvider(
        axes=axes,
        per_axis_candidates={
            "axis-a": [
                _candidate("low", "200 Vesey Street", "New York, NY", score=60),
                _candidate("high", "Brookfield Place North Tower", "New York, NY", score=85),
            ],
        },
        dedupe_decisions={
            "0": {"pair_id": "0", "same_project": True, "canonical_choice": "right"},
        },
    )
    result = discover_with_target(provider, _brief(), target_count=2, concurrency=1)
    ids = {c.comp_id for c in result.candidates}
    assert "high" in ids
    assert "low" not in ids


def test_replace_candidates_excludes_existing_and_rejected():
    axes = [{"label": "axis-a", "prompt_fragment": "x", "target_count": 3}]
    existing = [
        _candidate("keep1", "Existing One", "City X"),
        _candidate("keep2", "Existing Two", "City X"),
        _candidate("reject", "Rejected One", "City X"),
    ]
    provider = FakeProvider(
        axes=axes,
        per_axis_candidates={
            "axis-a": [_candidate(f"new{i}", f"Replacement {i}", "City Y") for i in range(3)],
        },
    )
    result = replace_candidates(
        provider,
        _brief(),
        axes=axes,
        existing=existing,
        exclude_ids=["reject"],
        count_needed=2,
        concurrency=1,
    )
    # Replacement call must have received an exclusion list covering both
    # accepted and rejected comps so the model doesn't echo any of them.
    excluded_names = {item["name"] for item in provider.axis_calls[0]["exclude"]}
    assert {"Existing One", "Existing Two", "Rejected One"}.issubset(excluded_names)
    assert result.delivered_count == 2
    # Returned candidates should not include any existing IDs.
    returned_ids = {c.comp_id for c in result.candidates}
    assert returned_ids.isdisjoint({c.comp_id for c in existing})


def test_replace_candidates_threads_steer_into_axis_call():
    axes = [{"label": "axis-a", "prompt_fragment": "x", "target_count": 1}]
    provider = FakeProvider(
        axes=axes,
        per_axis_candidates={"axis-a": [_candidate("new0", "New", "City")]},
    )
    replace_candidates(
        provider,
        _brief(),
        axes=axes,
        existing=[],
        exclude_ids=[],
        count_needed=1,
        steer="prefer newer projects",
        concurrency=1,
    )
    assert provider.axis_calls[0]["steer"] == "prefer newer projects"


def test_default_target_count_constant():
    # Document the user-facing default so a casual rename doesn't drift.
    assert DEFAULT_TARGET_COUNT == 10


def test_max_target_count_constant():
    # Document the hard cap so a casual rename doesn't drift.
    assert MAX_TARGET_COUNT == 50
