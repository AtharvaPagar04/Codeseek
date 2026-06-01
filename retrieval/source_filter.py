"""Display-time source filtering helpers."""

from __future__ import annotations

import re


def select_sources_for_display(raw_query: str, sources: list[dict]) -> list[dict]:
    """Prefer query-relevant primary citations and cap output noise."""
    query_tokens = query_tokens_from_text(raw_query)
    wants_tests = query_mentions_tests(raw_query)
    wants_compound = query_is_compound_trace(raw_query)
    wants_auth_trace = query_is_auth_flow_trace(raw_query)
    primary = [s for s in sources if s.get("expansion_type") == "primary"]
    expanded = [s for s in sources if s.get("expansion_type") != "primary"]

    def overlap(src: dict) -> int:
        return source_relevance_score(src, query_tokens)

    primary.sort(key=overlap, reverse=True)
    expanded.sort(key=overlap, reverse=True)

    if not wants_tests:
        primary_non_tests = [s for s in primary if not is_test_source(s)]
        expanded_non_tests = [s for s in expanded if not is_test_source(s)]
        if primary_non_tests:
            primary = primary_non_tests
        if expanded_non_tests:
            expanded = expanded_non_tests

    primary_relevant = [s for s in primary if overlap(s) > 0]
    expanded_relevant = [s for s in expanded if overlap(s) > 0]

    strong_threshold = 1 if wants_compound else 2
    strong_primary = [s for s in primary_relevant if overlap(s) >= strong_threshold]
    primary_cap = 7 if wants_auth_trace else (6 if wants_compound else 5)
    expanded_cap = 3 if wants_compound else 2
    chosen_primary = (
        strong_primary[:primary_cap]
        if strong_primary
        else (primary_relevant[:primary_cap] if primary_relevant else primary[:primary_cap])
    )
    chosen_primary = _inject_trace_anchors(raw_query, primary, chosen_primary, primary_cap)
    chosen_expanded = expanded_relevant[:expanded_cap]
    trimmed = chosen_primary + chosen_expanded

    seen = set()
    unique = []
    for src in trimmed:
        key = (
            src.get("relative_path", ""),
            src.get("symbol_name", ""),
            int(src.get("start_line", 0)),
            int(src.get("end_line", 0)),
            src.get("expansion_type", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(src)
    return unique


def explain_source_filter_decision(raw_query: str, sources: list[dict]) -> dict:
    """Return compact decision metadata for observability."""
    query_tokens = query_tokens_from_text(raw_query)
    wants_tests = query_mentions_tests(raw_query)
    wants_compound = query_is_compound_trace(raw_query)
    wants_auth_trace = query_is_auth_flow_trace(raw_query)
    primary = [s for s in sources if s.get("expansion_type") == "primary"]
    expanded = [s for s in sources if s.get("expansion_type") != "primary"]

    test_filtered = False
    if not wants_tests:
        primary_non_tests = [s for s in primary if not is_test_source(s)]
        expanded_non_tests = [s for s in expanded if not is_test_source(s)]
        if primary_non_tests and len(primary_non_tests) != len(primary):
            test_filtered = True
        if expanded_non_tests and len(expanded_non_tests) != len(expanded):
            test_filtered = True
        if primary_non_tests:
            primary = primary_non_tests
        if expanded_non_tests:
            expanded = expanded_non_tests

    primary_cap = 7 if wants_auth_trace else (6 if wants_compound else 5)
    expanded_cap = 3 if wants_compound else 2
    selected = select_sources_for_display(raw_query, sources)
    selected_primary = sum(1 for s in selected if s.get("expansion_type") == "primary")
    selected_expanded = len(selected) - selected_primary
    return {
        "query_tokens": sorted(query_tokens),
        "wants_tests": wants_tests,
        "wants_compound": wants_compound,
        "wants_auth_trace": wants_auth_trace,
        "test_filtered": test_filtered,
        "input_primary": len([s for s in sources if s.get("expansion_type") == "primary"]),
        "input_expanded": len([s for s in sources if s.get("expansion_type") != "primary"]),
        "selected_primary": selected_primary,
        "selected_expanded": selected_expanded,
        "primary_cap": primary_cap,
        "expanded_cap": expanded_cap,
    }


def _inject_trace_anchors(
    raw_query: str,
    all_primary: list[dict],
    chosen_primary: list[dict],
    cap: int,
) -> list[dict]:
    """For compound trace queries, include key flow symbols when available."""
    q = raw_query.lower()
    anchors: list[str] = []
    if any(k in q for k in ("account_info", "/api/v3/account", "authenticated request", "api key", "signature")):
        anchors.extend(["account_info", "authenticated_get", "signed_params", "sign_query", "auth_headers"])

    if not anchors:
        return chosen_primary

    chosen_ids = {
        (
            c.get("relative_path", ""),
            c.get("symbol_name", ""),
            int(c.get("start_line", 0)),
            int(c.get("end_line", 0)),
            c.get("expansion_type", ""),
        )
        for c in chosen_primary
    }
    result = list(chosen_primary)
    for anchor in anchors:
        if len(result) >= cap:
            break
        for src in all_primary:
            symbol = str(src.get("symbol_name", "")).lower()
            if symbol != anchor:
                continue
            key = (
                src.get("relative_path", ""),
                src.get("symbol_name", ""),
                int(src.get("start_line", 0)),
                int(src.get("end_line", 0)),
                src.get("expansion_type", ""),
            )
            if key in chosen_ids:
                break
            result.append(src)
            chosen_ids.add(key)
            break
    return result


def query_tokens_from_text(raw_query: str) -> set[str]:
    stop = {
        "where",
        "what",
        "which",
        "when",
        "does",
        "from",
        "with",
        "this",
        "that",
        "implemented",
        "function",
        "class",
        "trace",
        "exact",
        "show",
        "find",
        "list",
        "the",
        "and",
        "are",
        "is",
        "api",
        "request",
        "http",
        "final",
        "point",
        "attached",
        "identify",
        "method",
        "methods",
        "key",
    }
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", raw_query.lower()))
    return {t for t in tokens if t not in stop}


def query_mentions_tests(raw_query: str) -> bool:
    q = raw_query.lower()
    return any(term in q for term in ("test", "tests", "spec", "validation", "unit test"))


def query_is_compound_trace(raw_query: str) -> bool:
    q = raw_query.lower()
    markers = (" and ", "trace", "compare", "path", "flow", "where is", "where are")
    # Require at least one structural marker plus >1 significant tokens.
    has_marker = any(m in q for m in markers)
    return has_marker and len(query_tokens_from_text(raw_query)) >= 3


def query_is_auth_flow_trace(raw_query: str) -> bool:
    q = raw_query.lower()
    return (
        "trace" in q
        and any(k in q for k in ("account_info", "authenticated", "signature", "api key", "auth header"))
    )


def is_test_source(src: dict) -> bool:
    relative_path = str(src.get("relative_path", "")).lower()
    symbol_name = str(src.get("symbol_name", "")).lower()
    return "/test" in relative_path or relative_path.startswith("test") or symbol_name.startswith("test_")


def source_relevance_score(src: dict, query_tokens: set[str]) -> int:
    """Weighted lexical relevance for display-time source pruning."""
    symbol = str(src.get("symbol_name", "")).lower()
    relative_path = str(src.get("relative_path", "")).lower()
    hay = f"{relative_path} {symbol}"
    score = 0
    for token in query_tokens:
        token_singular = token[:-1] if token.endswith("s") else token
        if token in symbol or token_singular in symbol:
            score += 2
        elif token in relative_path or token_singular in relative_path:
            score += 1
        elif token in hay:
            score += 1
    return score
