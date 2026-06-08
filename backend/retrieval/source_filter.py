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

_OVERVIEW_NOISE_SYMBOLS = frozenset(
    {
        "_is_overview_query",
        "query_is_overview_summary",
        "build_overview_answer",
        "build_architecture_answer",
        "is_architecture_request",
        "is_overview_request",
        "_architecture_module_points",
        "_inject_architecture_files",
        "_inject_overview_candidates",
        "_preferred_overview_sources",
    }
)


def select_sources_for_display(raw_query: str, sources: list[dict]) -> list[dict]:
    """Prefer query-relevant primary citations and cap output noise."""
    query_tokens = query_tokens_from_text(raw_query)
    wants_tests = query_mentions_tests(raw_query)
    wants_compound = query_is_compound_trace(raw_query)
    wants_auth_trace = query_is_auth_flow_trace(raw_query)
    wants_phase1_flow = query_is_phase1_flow(raw_query)
    wants_overview = query_is_overview_summary(raw_query)
    wants_architecture = query_is_architecture_summary(raw_query)
    suppress_overview_meta = should_suppress_overview_meta_sources(raw_query)
    primary = [s for s in sources if s.get("expansion_type") == "primary"]
    expanded = [s for s in sources if s.get("expansion_type") != "primary"]

    if suppress_overview_meta:
        primary_filtered = _filter_overview_noise(primary)
        expanded_filtered = _filter_overview_noise(expanded)
        primary = primary_filtered
        expanded = expanded_filtered

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
    if wants_overview:
        unique = _prepend_overview_anchors(raw_query, sources, unique)
    if wants_architecture:
        unique = _prepend_architecture_anchors(raw_query, sources, unique)
    if suppress_overview_meta:
        filtered_unique = _filter_overview_noise(unique)
        unique = filtered_unique
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
    wants_overview = query_is_overview_summary(raw_query)
    suppress_overview_meta = should_suppress_overview_meta_sources(raw_query)
    display = select_sources_for_display(raw_query, assembled_sources)
    if suppress_overview_meta:
        filtered_display = _filter_overview_noise(display)
        display = filtered_display
    display = display[:DISPLAY_SOURCES_CAP]

    if not enabled:
        return display, list(display)

    display_keys: set[tuple] = {_source_key(s) for s in display}
    reasoning: list[dict] = list(display)

    remaining = [s for s in assembled_sources if _source_key(s) not in display_keys]
    if suppress_overview_meta:
        remaining_filtered = _filter_overview_noise(remaining)
        remaining = remaining_filtered
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


def _prepend_overview_anchors(raw_query: str, all_sources: list[dict], selected: list[dict]) -> list[dict]:
    """Front-load high-signal overview anchors so short prompts keep them after display capping."""
    if not query_is_overview_summary(raw_query):
        return selected

    query_tokens = query_tokens_from_text(raw_query)
    ranked = sorted(
        (
            src
            for src in all_sources
            if _overview_anchor_score(src) > 0 and not is_test_source(src)
        ),
        key=lambda src: (
            -_overview_anchor_score(src),
            -source_relevance_score(src, query_tokens),
            str(src.get("relative_path", "")),
            int(src.get("start_line", 0)),
        ),
    )
    anchors = ranked[:5]
    merged = anchors + list(selected)

    seen = set()
    unique = []
    for src in merged:
        key = _source_key(src)
        if key in seen:
            continue
        seen.add(key)
        unique.append(src)
    return unique


def _prepend_architecture_anchors(raw_query: str, all_sources: list[dict], selected: list[dict]) -> list[dict]:
    """Front-load runtime, ingestion, and config anchors for structure prompts."""
    if not query_is_architecture_summary(raw_query):
        return selected

    query_tokens = query_tokens_from_text(raw_query)
    ranked = sorted(
        (
            src
            for src in all_sources
            if _architecture_anchor_score(src) > 0 and not is_test_source(src)
        ),
        key=lambda src: (
            -_architecture_anchor_score(src),
            -source_relevance_score(src, query_tokens),
            str(src.get("relative_path", "")),
            int(src.get("start_line", 0)),
        ),
    )
    anchors = ranked[:6]
    merged = anchors + list(selected)

    seen = set()
    unique = []
    for src in merged:
        key = _source_key(src)
        if key in seen:
            continue
        seen.add(key)
        unique.append(src)
    return unique


def has_strong_source_location_evidence(
    raw_query: str,
    display_sources: list[dict],
    query_info: dict | None = None,
) -> bool:
    """Detect if this is a source-location query with strong evidence signals."""
    if not display_sources:
        return False
    if query_info is None:
        return False

    # Exclude general overview/architecture queries from source-location override
    q_lower = raw_query.lower()
    if any(w in q_lower for w in ("what does", "overview", "architecture", "tech stack", "summary")):
        if not any(loc_w in q_lower for loc_w in ("where", "file", "location", "folder", "directory", "path")):
            return False

    # Ensure the query actually seeks a location or contains location-seeking terms
    loc_terms = ("where", "file", "location", "folder", "directory", "path", "impl", "defined", "declared", "initialized", "source of", "source code", "happens")
    if not any(t in q_lower for t in loc_terms):
        return False

    from pathlib import Path
    top = display_sources[0]
    path = top.get("relative_path", "")
    symbol = top.get("symbol_name", "")
    labels = top.get("labels", [])

    # Get score
    score = top.get("score")
    if score is None:
        score = top.get("final_score")
    if score is None:
        score = top.get("retrieval_score")
    if score is None:
        score = 0.0

    q_lower = raw_query.lower()

    # 1. top result has exact file/path match
    if path:
        path_lower = path.lower()
        basename = Path(path).name.lower()
        if path_lower in q_lower or basename in q_lower:
            return True

    # 2. top result has symbol_name
    if symbol and symbol.strip():
        symbol_lower = symbol.lower()
        if symbol_lower in q_lower or any(part in q_lower for part in symbol_lower.split("_") if len(part) > 2):
            return True

    # 3. top result has labels including question_use:code-location or question_use:implementation
    if any(label in labels for label in ("question_use:code-location", "question_use:implementation")):
        return True

    # 4. top result is source-code and score/final_score is high
    is_source_code = False
    if path:
        suffix = Path(path).suffix.lower()
        if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".c", ".cpp", ".h"}:
            is_source_code = True
    if top.get("chunk_type") in {"function", "class", "method"}:
        is_source_code = True
    if "artifact:source-code" in labels:
        is_source_code = True

    if is_source_code and score >= 0.5:
        return True

    # 5. query intent is FILE/SYMBOL/CONFIG/DEPENDENCY and context_count > 0
    intent = str(query_info.get("intent", "")).upper()
    primary_intent = str(query_info.get("primary_intent", "")).upper()
    intents_to_check = {intent, primary_intent}
    target_intents = {"FILE", "SYMBOL", "CONFIG", "DEPENDENCY"}
    if (intents_to_check & target_intents) and len(display_sources) > 0:
        return True

    return False


def score_evidence_confidence(
    raw_query: str,
    display_sources: list[dict],
    query_info: dict | None = None,
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
    4. Strong source-location evidence → "strong" (override partial/weak default sizing)
    5. Fewer than 2 display sources → "partial"
    6. Top overlap score is 1 (single weak token hit) and fewer than 3 sources → "partial"
    7. Otherwise → "strong"
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

    # Overriding override for strong source-location evidence
    if has_strong_source_location_evidence(raw_query, display_sources, query_info):
        return {
            "level": "strong",
            "reason": "strong source-location evidence matched",
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
    if any(
        phrase in q
        for phrase in (
            "what is this project about",
            "whats this project about",
            "project overview",
            "overview of the project",
            "repository overview",
            "codebase overview",
            "give me a repository overview",
            "what does this project do",
            "what does this app do",
            "tech stack",
            "architecture overview",
            "architecture",
            "system design",
            "project structure",
            "repository structure",
            "codebase structure",
            "how is this project structured",
            "how is this codebase structured",
            "what are the main modules",
            "what are the core modules",
            "core modules in this codebase",
            "main modules in this codebase",
            "top-level subsystems",
            "top level subsystems",
            "module layout",
            "runtime shape",
        )
    ):
        return True

    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", q))
    if {"tech", "stack"} <= tokens:
        return True
    if ("module" in tokens or "modules" in tokens) and tokens & {"main", "core", "top", "level"}:
        return True
    if ("subsystem" in tokens or "subsystems" in tokens) and tokens & {"top", "level"}:
        return True
    if tokens & {"architecture", "structure", "overview", "repository", "codebase", "project"}:
        return bool(tokens & {"about", "summary", "describe", "what", "structured", "shape"})
    return False


def query_is_architecture_summary(raw_query: str) -> bool:
    q = raw_query.lower()
    if any(
        phrase in q
        for phrase in (
            "architecture overview",
            "architecture",
            "system design",
            "project structure",
            "repository structure",
            "codebase structure",
            "how is this project structured",
            "how is this codebase structured",
            "how is this repository structured",
            "main modules",
            "core modules",
            "top-level subsystems",
            "top level subsystems",
            "module layout",
            "runtime shape",
        )
    ):
        return True

    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", q))
    if ("module" in tokens or "modules" in tokens) and tokens & {"main", "core", "top", "level"}:
        return True
    if ("subsystem" in tokens or "subsystems" in tokens) and tokens & {"top", "level"}:
        return True
    if tokens & {"architecture", "structure", "modules", "subsystems"}:
        return bool(tokens & {"what", "describe", "structured", "shape", "overview", "main", "core", "top"})
    return False


def should_suppress_overview_meta_sources(raw_query: str) -> bool:
    """Return True for broad repo-structure prompts that should hide helper internals."""
    q = raw_query.lower()
    if query_is_overview_summary(raw_query):
        return True
    return any(
        phrase in q
        for phrase in (
            "main modules",
            "core modules",
            "top-level subsystems",
            "top level subsystems",
            "repository overview",
            "codebase overview",
            "codebase structured",
            "project structured",
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


def _filter_overview_noise(sources: list[dict]) -> list[dict]:
    """Remove meta-answering helper sources from repo-level overview queries."""
    kept: list[dict] = []
    for src in sources:
        path = str(src.get("relative_path", "")).strip().lower()
        symbol = str(src.get("symbol_name", "")).strip().lower()
        if _is_overview_noise_source(path, symbol):
            continue
        kept.append(src)
    return kept


def _overview_anchor_score(src: dict) -> int:
    relative_path = str(src.get("relative_path", "")).strip().lower()
    symbol_name = str(src.get("symbol_name", "")).strip().lower()
    chunk_type = str(src.get("chunk_type", "")).strip().lower()
    file_type = str(src.get("file_type", "")).strip().lower()

    if not relative_path or _is_overview_noise_source(relative_path, symbol_name):
        return 0
    if chunk_type == "repo_summary" or file_type == "repo_summary" or relative_path == "__repo_summary__.md":
        return 100
    if relative_path == "backend/readme.md":
        return 92
    if relative_path.endswith("backend/retrieval/api_service.py"):
        return 84
    if relative_path.endswith("backend/retrieval/main.py"):
        return 82
    if relative_path.endswith("backend/rag_ingestion/main.py"):
        return 80
    if relative_path.endswith("backend/docker-compose.yml"):
        return 70
    if relative_path.endswith("backend/retrieval/db.py"):
        return 68
    if relative_path == "readme.md":
        return 52
    if relative_path.endswith("docker-compose.yml"):
        return 48
    if relative_path.endswith(("requirements.txt", "pyproject.toml", "package.json", ".env.example")):
        return 40
    return 0


def _architecture_anchor_score(src: dict) -> int:
    relative_path = str(src.get("relative_path", "")).strip().lower()
    symbol_name = str(src.get("symbol_name", "")).strip().lower()
    chunk_type = str(src.get("chunk_type", "")).strip().lower()
    file_type = str(src.get("file_type", "")).strip().lower()

    if not relative_path or _is_overview_noise_source(relative_path, symbol_name):
        return 0
    if chunk_type == "repo_summary" or file_type == "repo_summary" or relative_path == "__repo_summary__.md":
        return 100
    if relative_path == "backend/readme.md":
        return 96
    if relative_path.endswith("backend/retrieval/api_service.py"):
        return 94
    if relative_path.endswith("backend/retrieval/main.py"):
        return 92
    if relative_path.endswith("backend/rag_ingestion/main.py"):
        return 90
    if relative_path.endswith("backend/docker-compose.yml"):
        return 88
    if relative_path.endswith("backend/.env.example"):
        return 86
    if relative_path.endswith("backend/docs/deployment_runbook.md"):
        return 84
    if relative_path.endswith("backend/retrieval/db.py"):
        return 82
    if relative_path == "readme.md":
        return 50
    if relative_path.endswith("docker-compose.yml"):
        return 48
    if relative_path.endswith(".env.example"):
        return 46
    if relative_path.endswith("docs/deployment_runbook.md"):
        return 44
    return 0


def _is_overview_noise_source(relative_path: str, symbol_name: str) -> bool:
    if symbol_name in _OVERVIEW_NOISE_SYMBOLS:
        return True

    if not relative_path.startswith("backend/"):
        return False

    if relative_path.endswith("retrieval/source_filter.py"):
        return True

    if relative_path.endswith("retrieval/query_processor.py") and symbol_name.startswith("_inject_"):
        return True

    if relative_path.endswith("retrieval/code_answers.py") and (
        symbol_name in _OVERVIEW_NOISE_SYMBOLS
        or symbol_name.startswith("_architecture_")
        or symbol_name.startswith("_overview_")
    ):
        return True

    if relative_path.endswith("retrieval/searcher.py") and (
        symbol_name in _OVERVIEW_NOISE_SYMBOLS
        or symbol_name.startswith("_is_overview")
        or symbol_name.startswith("_inject_overview")
    ):
        return True

    return False
