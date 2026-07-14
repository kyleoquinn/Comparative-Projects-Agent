"""Tests for takeaway/summary text fitting and degraded-run surfacing."""

from __future__ import annotations

import json

from comp_agent import deck, ui


ONE_LONG_SENTENCE = (
    "Comparable projects consistently pair a transparency-forward lobby with an activated "
    "public realm, food and beverage anchors, and flexible tenant amenity that pulls the "
    "street into the building and repositions the asset for premium tenant demand today."
)
MULTI_SENTENCE = (
    "Lobbies are being reopened to the street. Amenity floors anchor the tenant pitch. "
    "Public realm upgrades extend the workplace outdoors and lift the whole asset."
)


def test_fit_chars_counts_only_whole_lines():
    # 7.35in x 0.55in @13pt is ~2 whole lines; must be a sane, bounded budget.
    budget = deck._fit_chars(7.35, 0.55, 13)
    assert 100 <= budget <= 170
    assert deck._fit_chars(0, 1, 13) == 0
    assert deck._fit_chars(5, 5, 0) == 0


def test_fit_never_exceeds_and_never_cuts_mid_word():
    out = deck._fit(ONE_LONG_SENTENCE, 7.35, 0.55, 13)
    assert len(out) <= len(ONE_LONG_SENTENCE)
    assert len(out) < len(ONE_LONG_SENTENCE)  # it did trim
    # Ends cleanly: sentence terminator or an ellipsis on a word boundary.
    assert out.endswith((".", "!", "?", "…"))
    # The last word before any ellipsis must be a whole word from the source.
    core = out.rstrip("…").rstrip()
    assert core.split()[-1] in ONE_LONG_SENTENCE.split()


def test_fit_prefers_a_clean_sentence_boundary():
    # A generous box that fits ~2 sentences should end on a period, no ellipsis.
    out = deck._fit(MULTI_SENTENCE, 7.35, 0.55, 13)
    assert out.endswith(".")
    assert "…" not in out


def test_short_text_passes_through_untouched():
    assert deck._fit("A tidy one-liner.", 7.35, 0.55, 13) == "A tidy one-liner."


def test_smart_trim_word_boundary_with_ellipsis():
    assert deck._smart_trim("alpha beta gamma delta epsilon", 14) == "alpha beta…"


def test_truncate_for_display_is_word_aware():
    # Previously cut mid-word; now must not.
    out = deck._truncate_for_display("transparency forward activation strategy", 20)
    assert "…" in out
    assert out.replace("…", "").strip().split()[-1] in {"transparency", "forward"}


def test_degradation_status_flags_quota_and_is_quiet_otherwise(tmp_path):
    clean = tmp_path / "repaired_comps.json"
    clean.write_text(json.dumps([{"source_notes": ["all good"]}]), encoding="utf-8")
    assert ui._degradation_status({"repaired_comps": str(clean)}) is None
    assert ui._degradation_status({}) is None

    degraded = tmp_path / "repaired_comps.json"
    degraded.write_text(
        json.dumps([{"source_notes": ["OpenAI live search failed with HTTP 429: insufficient_quota"]}]),
        encoding="utf-8",
    )
    status = ui._degradation_status({"repaired_comps": str(degraded)})
    assert status and status["headline"] == "Deck Generated With Reduced Data"
    assert "credit" in status["detail"].lower()
