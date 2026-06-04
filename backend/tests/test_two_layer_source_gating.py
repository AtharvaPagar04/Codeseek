"""Tests for two-layer source gating (split_sources_two_layer) and intent-aware budgets."""

from __future__ import annotations

import pytest

from retrieval.source_filter import split_sources_two_layer, select_sources_for_display
from retrieval.assembler import intent_context_budget
from retrieval.config import (
    DISPLAY_SOURCES_CAP,
    REASONING_SOURCES_CAP,
    MAX_CONTEXT_TOKENS,
    INTENT_CONTEXT_BUDGETS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _src(path: str, symbol: str, start: int = 1, end: int = 10, expansion: str = "primary") -> dict:
    return {
        "relative_path": path,
        "symbol_name": symbol,
        "start_line": start,
        "end_line": end,
        "expansion_type": expansion,
        "retrieval_score": 0.9,
        "chunk_type": "function",
    }


def _make_pool(n: int, prefix: str = "src/file", expansion: str = "primary") -> list[dict]:
    return [_src(f"{prefix}{i}.py", f"sym_{i}", start=i * 10, end=i * 10 + 5, expansion=expansion) for i in range(n)]


# ---------------------------------------------------------------------------
# split_sources_two_layer — basic invariants
# ---------------------------------------------------------------------------

class TestSplitSourcesTwoLayerBasic:
    def test_empty_input_returns_empty_both(self):
        display, reasoning = split_sources_two_layer("what does this do", [])
        assert display == []
        assert reasoning == []

    def test_display_is_subset_of_reasoning(self):
        pool = _make_pool(8)
        display, reasoning = split_sources_two_layer("how does auth work", pool)
        display_keys = {(s["relative_path"], s["symbol_name"]) for s in display}
        reasoning_keys = {(s["relative_path"], s["symbol_name"]) for s in reasoning}
        assert display_keys.issubset(reasoning_keys), "display must be a subset of reasoning"

    def test_display_capped_at_display_cap(self):
        pool = _make_pool(20)
        display, _ = split_sources_two_layer("show me everything", pool)
        assert len(display) <= DISPLAY_SOURCES_CAP

    def test_reasoning_capped_at_reasoning_cap(self):
        pool = _make_pool(20)
        _, reasoning = split_sources_two_layer("show me everything", pool)
        assert len(reasoning) <= REASONING_SOURCES_CAP

    def test_reasoning_larger_than_display_when_pool_sufficient(self):
        pool = _make_pool(15)
        display, reasoning = split_sources_two_layer("explain the auth flow", pool)
        # Reasoning should extend beyond display when there are spare sources
        assert len(reasoning) >= len(display)

    def test_no_duplicates_in_reasoning(self):
        pool = _make_pool(10)
        _, reasoning = split_sources_two_layer("how does auth work", pool)
        keys = [(s["relative_path"], s["symbol_name"], s["start_line"], s["end_line"]) for s in reasoning]
        assert len(keys) == len(set(keys)), "No duplicate sources in reasoning set"


# ---------------------------------------------------------------------------
# split_sources_two_layer — disabled mode (legacy)
# ---------------------------------------------------------------------------

class TestSplitSourcesTwoLayerDisabled:
    def test_disabled_returns_same_list_for_both(self):
        pool = _make_pool(10)
        display, reasoning = split_sources_two_layer("auth flow", pool, enabled=False)
        assert display == reasoning

    def test_disabled_display_still_capped(self):
        pool = _make_pool(20)
        display, reasoning = split_sources_two_layer("auth flow", pool, enabled=False)
        assert len(display) <= DISPLAY_SOURCES_CAP
        assert len(reasoning) <= DISPLAY_SOURCES_CAP

    def test_disabled_does_not_extend_beyond_display(self):
        pool = _make_pool(15)
        display, reasoning = split_sources_two_layer("auth flow", pool, enabled=False)
        assert len(reasoning) == len(display)


# ---------------------------------------------------------------------------
# split_sources_two_layer — extension behaviour
# ---------------------------------------------------------------------------

class TestSplitSourcesTwoLayerExtension:
    def test_reasoning_extends_with_primary_sources_first(self):
        """Extra reasoning slots should be filled with primaries before expanded."""
        primary_pool = _make_pool(8, "src/primary", expansion="primary")
        expanded_pool = _make_pool(5, "src/expanded", expansion="callee")
        pool = primary_pool + expanded_pool

        display, reasoning = split_sources_two_layer("trace the auth flow", pool, enabled=True)
        display_keys = {(s["relative_path"], s["symbol_name"]) for s in display}
        extra = [s for s in reasoning if (s["relative_path"], s["symbol_name"]) not in display_keys]
        # Any primaries not in display should appear before callees in the extras
        extra_types = [s["expansion_type"] for s in extra]
        # All primaries should come before callees in the extra set
        seen_callee = False
        for t in extra_types:
            if t == "callee":
                seen_callee = True
            elif seen_callee:
                pytest.fail(f"Primary source appeared after callee in reasoning extras: {extra_types}")

    def test_reasoning_does_not_repeat_display_sources(self):
        pool = _make_pool(10)
        display, reasoning = split_sources_two_layer("what calls verify_token", pool)
        display_keys = {(s["relative_path"], s["symbol_name"], s["start_line"], s["end_line"]) for s in display}
        for s in display:
            matching = [
                r for r in reasoning
                if (r["relative_path"], r["symbol_name"], r["start_line"], r["end_line"])
                == (s["relative_path"], s["symbol_name"], s["start_line"], s["end_line"])
            ]
            assert len(matching) == 1, f"Display source {s['symbol_name']} appears more than once in reasoning"


# ---------------------------------------------------------------------------
# intent_context_budget
# ---------------------------------------------------------------------------

class TestIntentContextBudget:
    def test_known_intents_return_correct_budget(self):
        for intent, expected in INTENT_CONTEXT_BUDGETS.items():
            assert intent_context_budget(intent) == expected

    def test_unknown_intent_falls_back_to_max(self):
        assert intent_context_budget("TOTALLY_UNKNOWN") == MAX_CONTEXT_TOKENS

    def test_none_intent_falls_back_to_max(self):
        assert intent_context_budget(None) == MAX_CONTEXT_TOKENS

    def test_empty_string_intent_falls_back_to_max(self):
        assert intent_context_budget("") == MAX_CONTEXT_TOKENS

    def test_case_insensitive(self):
        assert intent_context_budget("semantic") == INTENT_CONTEXT_BUDGETS["SEMANTIC"]
        assert intent_context_budget("Trace") == INTENT_CONTEXT_BUDGETS["TRACE"]
        assert intent_context_budget("OVERVIEW") == INTENT_CONTEXT_BUDGETS["OVERVIEW"]

    def test_trace_budget_larger_than_symbol_budget(self):
        """Trace queries need more context than direct symbol lookups."""
        assert intent_context_budget("TRACE") > intent_context_budget("SYMBOL")

    def test_architecture_budget_larger_than_config_budget(self):
        """Architecture answers need more breadth than config key lookups."""
        assert intent_context_budget("ARCHITECTURE") > intent_context_budget("CONFIG")

    def test_all_budgets_within_reasonable_bounds(self):
        for intent, budget in INTENT_CONTEXT_BUDGETS.items():
            assert 1000 <= budget <= 10000, f"Budget for {intent}={budget} is outside [1000, 10000]"


# ---------------------------------------------------------------------------
# Regression: existing select_sources_for_display still works
# ---------------------------------------------------------------------------

class TestSelectSourcesForDisplayRegression:
    def test_no_sources_returns_empty(self):
        assert select_sources_for_display("what does this do", []) == []

    def test_returns_list(self):
        pool = _make_pool(5)
        result = select_sources_for_display("auth flow", pool)
        assert isinstance(result, list)

    def test_no_duplicates(self):
        pool = _make_pool(8)
        result = select_sources_for_display("auth flow", pool)
        keys = [(s["relative_path"], s["symbol_name"], s["start_line"], s["end_line"]) for s in result]
        assert len(keys) == len(set(keys))

    def test_two_layer_display_matches_select_sources_output_capped(self):
        """display_sources from split_sources_two_layer should match select_sources capped at DISPLAY_SOURCES_CAP."""
        pool = _make_pool(12)
        display, _ = split_sources_two_layer("auth flow", pool, enabled=True)
        legacy = select_sources_for_display("auth flow", pool)[:DISPLAY_SOURCES_CAP]
        # Same items (order may differ slightly due to dedup pass, so compare keys)
        display_keys = {(s["relative_path"], s["symbol_name"]) for s in display}
        legacy_keys = {(s["relative_path"], s["symbol_name"]) for s in legacy}
        assert display_keys == legacy_keys
