import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from retrieval.source_filter import (
    query_tokens_from_text, query_mentions_tests, query_is_compound_trace,
    query_is_auth_flow_trace, query_is_phase1_flow, query_is_overview_summary,
    query_is_architecture_summary, _query_is_indexing_explanation,
    _query_is_retrieval_explanation, _query_is_frontend_ui_location,
    _source_contract_intent, should_suppress_overview_meta_sources,
    _primary_source_cap, _query_prefers_implementation_sources,
    _inject_trace_anchors, _inject_phase1_flow_anchors,
    _prepend_overview_anchors, _prepend_architecture_anchors,
    _prepend_indexing_anchors, _prepend_retrieval_anchors,
    _prepend_contract_anchors, refine_overview_display_sources,
    _filter_overview_noise, _frontend_ui_source_score,
    _source_contract_score, _intent_display_priority,
    source_relevance_score, _query_is_retrieval_pipeline_flow,
    is_test_source, _source_key,
    apply_query_negative_filters, _source_allowed_for_reasoning
)
from retrieval.searcher import match_code_topic_route, path_matches_topic_route

raw_query = "What is this project about?"
sources = [
    {
        "relative_path": "README.md",
        "symbol_name": "README",
        "start_line": 1,
        "end_line": 20,
        "expansion_type": "primary",
    },
    {
        "relative_path": "frontend/package.json",
        "symbol_name": "package_json",
        "start_line": 1,
        "end_line": 30,
        "expansion_type": "primary",
    },
    {
        "relative_path": "__repo_summary__.md",
        "symbol_name": "repo_summary",
        "chunk_type": "repo_summary",
        "file_type": "repo_summary",
        "start_line": 1,
        "end_line": 12,
        "expansion_type": "primary",
    },
    {
        "relative_path": "backend/README.md",
        "symbol_name": "README",
        "start_line": 1,
        "end_line": 40,
        "expansion_type": "primary",
    },
    {
        "relative_path": "backend/retrieval/api_service.py",
        "symbol_name": "_query_impl",
        "start_line": 512,
        "end_line": 678,
        "expansion_type": "primary",
    },
    {
        "relative_path": "backend/retrieval/main.py",
        "symbol_name": "run_query",
        "start_line": 88,
        "end_line": 553,
        "expansion_type": "primary",
    },
]

# Run the function step-by-step
query_tokens = query_tokens_from_text(raw_query)
wants_tests = query_mentions_tests(raw_query)
wants_compound = query_is_compound_trace(raw_query)
wants_auth_trace = query_is_auth_flow_trace(raw_query)
wants_phase1_flow = query_is_phase1_flow(raw_query)
wants_overview = query_is_overview_summary(raw_query)
wants_architecture = query_is_architecture_summary(raw_query)
wants_indexing = _query_is_indexing_explanation(raw_query)
wants_retrieval = _query_is_retrieval_explanation(raw_query)
wants_ui_location = _query_is_frontend_ui_location(raw_query)
source_contract_intent = _source_contract_intent(raw_query)
suppress_overview_meta = should_suppress_overview_meta_sources(raw_query)
primary = [s for s in sources if s.get("expansion_type") == "primary"]
expanded = [s for s in sources if s.get("expansion_type") != "primary"]

primary_cap = _primary_source_cap(raw_query, wants_auth_trace, wants_phase1_flow, wants_compound, wants_overview)
expanded_cap = 3 if (wants_compound or wants_overview) else 2

chosen_primary = primary[:primary_cap]
chosen_expanded = expanded[:expanded_cap]
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
print(f"unique after _prepend_overview_anchors (len={len(unique)}):", [s["relative_path"] for s in unique])

if wants_overview or wants_architecture:
    unique = refine_overview_display_sources(raw_query, unique, sources)
print(f"unique after refine_overview_display_sources (len={len(unique)}):", [s["relative_path"] for s in unique])

if suppress_overview_meta:
    unique = _filter_overview_noise(unique)
print(f"unique after _filter_overview_noise (len={len(unique)}):", [s["relative_path"] for s in unique])

# Final sorting
unique = sorted(
    unique,
    key=lambda src: (
        -(
            _frontend_ui_source_score(src)
            if wants_ui_location
            else (
                _source_contract_score(raw_query, src)
                if source_contract_intent != "general"
                else
                _intent_display_priority(
                    src,
                    wants_architecture=wants_architecture,
                    wants_indexing=wants_indexing,
                    wants_retrieval=wants_retrieval,
                )
            )
        ),
        -source_relevance_score(src, query_tokens),
        str(src.get("relative_path", "")),
        int(src.get("start_line", 0)),
        int(src.get("end_line", 0)),
    ),
)
print(f"unique after sorting (len={len(unique)}):", [s["relative_path"] for s in unique])

matched_code_topic_route = match_code_topic_route(raw_query, "CODE_REQUEST")
unique = apply_query_negative_filters(
    unique,
    raw_query,
    matched_route=matched_code_topic_route,
)
print(f"unique after apply_query_negative_filters (len={len(unique)}):", [s["relative_path"] for s in unique])

if wants_ui_location or wants_indexing or wants_retrieval or suppress_overview_meta or source_contract_intent != "general":
    aligned_unique = [src for src in unique if _source_allowed_for_reasoning(raw_query, src)]
    if aligned_unique:
        unique = aligned_unique
print(f"unique after _source_allowed_for_reasoning (len={len(unique)}):", [s["relative_path"] for s in unique])
