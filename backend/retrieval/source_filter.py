"""Display-time source filtering helpers.

Two-layer source model
----------------------
display_sources   — strict citation set, max DISPLAY_SOURCES_CAP (6).
                    Shown to the user as source cards.
                    Injected into the LLM prompt as the ALLOWED SOURCES list.
reasoning_sources — broader synthesis set, max REASONING_SOURCES_CAP (12).
                    Must be a superset of display_sources.
                    Used to assemble the CODE CONTEXT block passed to the LLM.
                    Never cited directly unless promoted into display_sources.

When RETRIEVAL_ENABLE_TWO_LAYER_SOURCES=0 (or the flag is absent and disabled),
both lists collapse to the same single-list behaviour as before.
"""

from __future__ import annotations

import re

from retrieval.config import DISPLAY_SOURCES_CAP, REASONING_SOURCES_CAP


def select_sources_for_display(raw_query: str, sources: list[dict]) -> list[dict]:
    """Prefer query-relevant primary citations and cap output noise."""
    query_tokens = query_tokens_from_text(raw_query)
    wants_tests = query_mentions_tests(raw_query)
    wants_compound = query_is_compound_trace(raw_query)
    wants_auth_trace = query_is_auth_flow_trace(raw_query)
    wants_phase1_flow = query_is_phase1_flow(raw_query)
    wants_overview = query_is_overview_summary(raw_query)
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

    strong_threshold = 1 if (wants_compound or wants_overview) else 2
    strong_primary = [s for s in primary_relevant if overlap(s) >= strong_threshold]
    primary_cap = _primary_source_cap(raw_query, wants_auth_trace, wants_phase1_flow, wants_compound, wants_overview)
    expanded_cap = 3 if (wants_compound or wants_overview) else 2
    chosen_primary = (
        strong_primary[:primary_cap]
        if strong_primary
        else (primary_relevant[:primary_cap] if primary_relevant else primary[:primary_cap])
    )
    chosen_primary = _inject_trace_anchors(raw_query, primary, chosen_primary, primary_cap)
    chosen_primary = _inject_phase1_flow_anchors(raw_query, primary, chosen_primary, primary_cap)
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


def split_sources_two_layer(
    raw_query: str,
    assembled_sources: list[dict],
    enabled: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Return (display_sources, reasoning_sources) implementing the two-layer model.

    display_sources
        Strict citation set capped at DISPLAY_SOURCES_CAP (default 6).
        Derived from select_sources_for_display().
        Used for user-facing source cards and the LLM ALLOWED SOURCES list.

    reasoning_sources
        Broader synthesis set capped at REASONING_SOURCES_CAP (default 12).
        Always a superset of display_sources.
        Provides extra context for LLM synthesis without relaxing citation safety.

    When enabled=False both lists are identical to display_sources (legacy behaviour).
    """
    display = select_sources_for_display(raw_query, assembled_sources)
    display = display[:DISPLAY_SOURCES_CAP]

    if not enabled:
        return display, list(display)

    display_keys: set[tuple] = {_source_key(s) for s in display}
    reasoning: list[dict] = list(display)

    remaining = [s for s in assembled_sources if _source_key(s) not in display_keys]
    primary_remaining = [s for s in remaining if s.get("expansion_type") == "primary"]
    expanded_remaining = [s for s in remaining if s.get("expansion_type") != "primary"]

    for candidate in primary_remaining + expanded_remaining:
        if len(reasoning) >= REASONING_SOURCES_CAP:
            break
        key = _source_key(candidate)
        if key in display_keys:
            continue
        reasoning.append(candidate)
        display_keys.add(key)

    return display, reasoning


def _source_key(src: dict) -> tuple:
    return (
        src.get("relative_path", ""),
        src.get("symbol_name", ""),
        int(src.get("start_line", 0)),
        int(src.get("end_line", 0)),
        src.get("expansion_type", ""),
    )


def score_evidence_confidence(
    raw_query: str,
    display_sources: list[dict],
) -> dict:
    """Classify the quality of the assembled evidence for this query.

    Returns a dict with:
        level   — "strong" | "partial" | "weak"
        reason  — short human-readable explanation (for observability / logging)
        count   — number of display sources considered

    Classification rules (in priority order):
    1. No sources at all → "weak"
    2. No primary sources → "weak"  (all sources are expansion-only, low confidence)
    3. Top source has zero lexical overlap with the query → "weak"
    4. Fewer than 2 display sources → "partial"
    5. Top overlap score is 1 (single weak token hit) and fewer than 3 sources → "partial"
    6. Otherwise → "strong"
    """
    count = len(display_sources)
    if count == 0:
        return {"level": "weak", "reason": "no sources assembled", "count": 0}

    has_primary = any(s.get("expansion_type") == "primary" for s in display_sources)
    if not has_primary:
        return {
            "level": "weak",
            "reason": "no primary sources; only expansion results",
            "count": count,
        }

    query_tokens = query_tokens_from_text(raw_query)
    top_score = max(source_relevance_score(s, query_tokens) for s in display_sources)

    if top_score == 0:
        return {
            "level": "weak",
            "reason": "top source has zero lexical overlap with query",
            "count": count,
        }

    if count < 2:
        return {
            "level": "partial",
            "reason": f"only {count} display source(s) assembled",
            "count": count,
        }

    if top_score == 1 and count < 3:
        return {
            "level": "partial",
            "reason": "low relevance score with limited source coverage",
            "count": count,
        }

    return {"level": "strong", "reason": "adequate sources with lexical overlap", "count": count}


def explain_source_filter_decision(raw_query: str, sources: list[dict]) -> dict:
    """Return compact decision metadata for observability."""
    query_tokens = query_tokens_from_text(raw_query)
    wants_tests = query_mentions_tests(raw_query)
    wants_compound = query_is_compound_trace(raw_query)
    wants_auth_trace = query_is_auth_flow_trace(raw_query)
    wants_phase1_flow = query_is_phase1_flow(raw_query)
    wants_overview = query_is_overview_summary(raw_query)
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

    primary_cap = _primary_source_cap(raw_query, wants_auth_trace, wants_phase1_flow, wants_compound, wants_overview)
    expanded_cap = 3 if (wants_compound or wants_overview) else 2
    selected = select_sources_for_display(raw_query, sources)
    selected_primary = sum(1 for s in selected if s.get("expansion_type") == "primary")
    selected_expanded = len(selected) - selected_primary
    display, reasoning = split_sources_two_layer(raw_query, sources)
    return {
        "query_tokens": sorted(query_tokens),
        "wants_tests": wants_tests,
        "wants_compound": wants_compound,
        "wants_auth_trace": wants_auth_trace,
        "wants_phase1_flow": wants_phase1_flow,
        "wants_overview": wants_overview,
        "test_filtered": test_filtered,
        "input_primary": len([s for s in sources if s.get("expansion_type") == "primary"]),
        "input_expanded": len([s for s in sources if s.get("expansion_type") != "primary"]),
        "selected_primary": selected_primary,
        "selected_expanded": selected_expanded,
        "primary_cap": primary_cap,
        "expanded_cap": expanded_cap,
        "display_count": len(display),
        "reasoning_count": len(reasoning),
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


def _primary_source_cap(
    raw_query: str,
    wants_auth_trace: bool,
    wants_phase1_flow: bool,
    wants_compound: bool,
    wants_overview: bool,
) -> int:
    q = raw_query.lower()
    if wants_phase1_flow and any(term in q for term in ("provider", "credential", "credentials", "api key", "llm", "model")):
        return 9
    if wants_auth_trace or wants_phase1_flow:
        return 7
    if wants_compound or wants_overview:
        return 6
    return 5


def _inject_phase1_flow_anchors(
    raw_query: str,
    all_primary: list[dict],
    chosen_primary: list[dict],
    cap: int,
) -> list[dict]:
    anchors = _phase1_flow_anchors(raw_query)
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
    anchor_sources: list[dict] = []
    for anchor in anchors:
        for src in all_primary:
            symbol = str(src.get("symbol_name", ""))
            path = str(src.get("relative_path", ""))
            if symbol != anchor and path != anchor and not path.endswith(f"/{anchor}"):
                continue
            key = (
                src.get("relative_path", ""),
                src.get("symbol_name", ""),
                int(src.get("start_line", 0)),
                int(src.get("end_line", 0)),
                src.get("expansion_type", ""),
            )
            if key in chosen_ids:
                anchor_sources.extend(
                    chosen
                    for chosen in chosen_primary
                    if (
                        chosen.get("relative_path", ""),
                        chosen.get("symbol_name", ""),
                        int(chosen.get("start_line", 0)),
                        int(chosen.get("end_line", 0)),
                        chosen.get("expansion_type", ""),
                    )
                    == key
                )
                break
            anchor_sources.append(src)
            chosen_ids.add(key)
            break
    result: list[dict] = []
    result_ids: set[tuple[str, str, int, int, str]] = set()
    for src in anchor_sources + chosen_primary:
        key = (
            src.get("relative_path", ""),
            src.get("symbol_name", ""),
            int(src.get("start_line", 0)),
            int(src.get("end_line", 0)),
            src.get("expansion_type", ""),
        )
        if key in result_ids:
            continue
        result.append(src)
        result_ids.add(key)
        if len(result) >= cap:
            break
    return result


def _phase1_flow_anchors(raw_query: str) -> list[str]:
    q = raw_query.lower()
    if not query_is_phase1_flow(raw_query):
        return []
    if any(term in q for term in ("auth", "oauth", "login", "cookie", "credential")):
        return [
            "auth_github",
            "auth_github_callback",
            "auth_github_token",
            "create_auth_session",
            "get_user_for_session_token",
            "_require_auth_user",
            "delete_auth_session",
            "auth_logout",
        ]
    if any(term in q for term in ("index", "indexing", "ingestion", "repo session", "session creation", "clone")):
        return ["create_session", "_index_job", "run_pipeline"]
    if any(term in q for term in ("deploy", "deployment", "docker", "compose", "container", "environment", "configuration", "config")):
        return ["docker-compose.yml", "Dockerfile", ".env.example", "deployment_runbook", "run_local_backend"]
    if any(term in q for term in ("provider", "credential", "credentials", "api key", "llm", "model")):
        return [
            "list_provider_credentials_v1",
            "create_provider_credential_v1",
            "create_provider_credential",
            "set_active_provider_credential",
            "delete_provider_credential",
            "get_active_provider_credential",
        ]
    if any(term in q for term in ("backend", "request", "query", "orchestration", "api")):
        return ["_query_impl", "run_query"]
    return []


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


def query_is_phase1_flow(raw_query: str) -> bool:
    q = raw_query.lower()
    if not any(
        marker in q
        for marker in (
            "flow",
            "lifecycle",
            "orchestration",
            "trace",
            "walk me through",
            "step",
            "deployment",
            "configuration",
            "config",
            "provider",
            "credential",
            "credentials",
            "api key",
            "llm",
            "model",
        )
    ):
        return False
    return any(
        term in q
        for term in (
            "backend",
            "request",
            "query",
            "api",
            "auth",
            "oauth",
            "login",
            "cookie",
            "credential",
            "index",
            "indexing",
            "ingestion",
            "repo session",
            "session creation",
            "clone",
            "deploy",
            "deployment",
            "docker",
            "compose",
            "container",
            "environment",
            "configuration",
            "config",
            "provider",
            "credential",
            "credentials",
            "api key",
            "llm",
            "model",
        )
    )


def query_is_overview_summary(raw_query: str) -> bool:
    q = raw_query.lower()
    return any(
        phrase in q
        for phrase in (
            "what is this project about",
            "whats this project about",
            "project overview",
            "overview of the project",
            "what does this project do",
            "what does this app do",
            "tech stack",
            "architecture overview",
            "architecture",
            "system design",
            "project structure",
            "how is this project structured",
            "module layout",
            "runtime shape",
        )
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
