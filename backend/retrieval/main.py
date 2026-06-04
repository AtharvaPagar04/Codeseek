"""Entry point for retrieval pipeline."""

import argparse
import os
import re
import time

from retrieval.assembler import assemble, assemble_for_reasoning, intent_history_cap
from retrieval.code_answers import (
    build_architecture_answer,
    build_explanation_answer,
    build_code_answer,
    build_flow_answer,
    build_overview_answer,
    build_symbol_deep_dive_answer,
    find_supporting_import_exports,
    is_code_request,
    is_architecture_request,
    is_explanation_request,
    is_flow_explanation_request,
    is_overview_request,
    is_symbol_deep_dive_request,
)
from retrieval.config import (
    CONVERSATION_HISTORY_TURNS,
    ENABLE_TWO_LAYER_SOURCES,
    MAX_CONTEXT_TOKENS,
    get_collection_name,
    get_repo_root,
)
from retrieval.expander import expand
from retrieval.follow_up_memory import (
    build_recent_entity_set,
    detect_topic_shift,
    extract_cited_entities,
    rewrite_follow_up_query,
)
from retrieval.llm import generate_answer
from retrieval.memory import ConversationMemory
from retrieval.observability import StageMetrics, log_event, new_request_id
from retrieval.query_processor import process_query
from retrieval.isolation import validate_collection_binding
from retrieval.searcher import search
from retrieval.source_filter import (
    explain_source_filter_decision,
    score_evidence_confidence,
    select_sources_for_display,
    split_sources_two_layer,
)


FOLLOW_UP_MARKERS = {
    "also",
    "again",
    "same",
    "code",
    "snippet",
    "implementation",
    "example",
    "expand",
    "more",
    "details",
    "it",
    "that",
    "those",
    "this",
}

LOW_CONTEXT_FALLBACK = (
    "No relevant code was found for this query. "
    "Try rephrasing with a specific file name, function name, class, route, or config key. "
    "Example: \"how does `create_session` work\" or \"what does auth.py do\"."
)

PARTIAL_EVIDENCE_BANNER = (
    "⚠ **Partial evidence:** this answer is based on a small or weakly-matched source set "
    "and may be missing important details. "
    "For a more complete answer, try naming a specific file, function, or class.\n\n"
)

WEAK_EVIDENCE_BANNER = (
    "⚠ **Low confidence:** the retrieved sources have weak relevance to this query. "
    "The answer below may be incomplete or inaccurate — treat it as a starting point only. "
    "Try a more targeted question naming a specific symbol, file, or route.\n\n"
)


def run_query(
    raw_query: str,
    memory: ConversationMemory,
    request_id: str | None = None,
    return_meta: bool = False,
    provider_config: dict | None = None,
) -> tuple[str, list[dict], int] | tuple[str, list[dict], int, dict]:
    """Run one retrieval query end-to-end."""
    rid = request_id or new_request_id()
    metrics = StageMetrics(request_id=rid)
    meta: dict = {"request_id": rid}
    log_event("retrieval.request.start", rid, query=raw_query)
    validate_collection_binding(get_collection_name(), get_repo_root())
    started = time.perf_counter()
    history_block = memory.get_history_block()  # full, for search/follow-up rewrite
    metrics.add_stage("history", started)
    started = time.perf_counter()
    # WS7: load recent cited entities and pass them into query resolution.
    recent_turns = memory.recent_turn_entities(max_turns=8) if hasattr(memory, "recent_turn_entities") else []
    query_info = _resolve_query_info(raw_query, memory, recent_turns=recent_turns)
    metrics.add_stage("query_processor", started)
    # Resolve intent early so the history cap can be applied before assembly.
    primary_intent = query_info.get("primary_intent") or query_info.get("intent")
    history_cap = intent_history_cap(primary_intent)
    history_block_capped = memory.get_history_block_capped(history_cap)
    started = time.perf_counter()
    candidates = search(query_info)
    metrics.add_stage("search", started)
    started = time.perf_counter()
    expanded = expand(candidates, query_info)
    metrics.add_stage("expand", started)
    started = time.perf_counter()
    context, sources, token_count = assemble(expanded, history_block_capped)
    metrics.add_stage("assemble", started)
    meta["source_filter"] = explain_source_filter_decision(raw_query, sources)
    # Two-layer source gating: display_sources for citations, reasoning_sources for context.
    display_sources, reasoning_sources = split_sources_two_layer(
        raw_query, sources, enabled=ENABLE_TWO_LAYER_SOURCES
    )
    shown_sources = display_sources
    evidence_confidence = score_evidence_confidence(raw_query, shown_sources)
    if is_flow_explanation_request(raw_query):
        flow_sources = select_sources_for_display(raw_query, expanded)
        if flow_sources:
            shown_sources = flow_sources
    if not shown_sources:
        answer = LOW_CONTEXT_FALLBACK
        cited_entities = {}
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta.update(
            {
                "stage_latency_ms": metrics.stage_latency_ms,
                "total_latency_ms": metrics.total_ms(),
                "backend_latency_ms": metrics.total_ms(),
                "provider_latency_ms": 0,
                "errors": metrics.errors,
                "response_mode": "low_context",
                "evidence_confidence": {"level": "weak", "reason": "no sources assembled", "count": 0},
            }
        )
        log_event(
            "retrieval.request.end",
            rid,
            status="ok",
            fallback="no_sources",
            collection=get_collection_name(),
            repo_root=get_repo_root(),
            intent=query_info.get("intent"),
            entities=query_info.get("entities", {}),
            candidates=len(candidates),
            expanded=len(expanded),
            assembled_sources=len(sources),
            stage_latency_ms=metrics.stage_latency_ms,
            total_latency_ms=metrics.total_ms(),
            source_filter=meta["source_filter"],
        )
        if return_meta:
            return answer, shown_sources, token_count, meta
        return answer, shown_sources, token_count
    # Build chunk list for deterministic answer paths: filtered to shown (display) sources only.
    allowed_keys = {
        (
            s.get("relative_path", ""),
            s.get("symbol_name", ""),
            int(s.get("start_line", 0)),
            int(s.get("end_line", 0)),
            s.get("expansion_type", "primary"),
        )
        for s in shown_sources
    }
    llm_chunks = [
        c
        for c in expanded
        if (
            c.get("relative_path", ""),
            c.get("symbol_name", ""),
            int(c.get("start_line", 0)),
            int(c.get("end_line", 0)),
            c.get("expansion_type", "primary"),
        )
        in allowed_keys
    ]
    if llm_chunks:
        context, _, token_count = assemble(llm_chunks, history_block_capped)
    # For the LLM path: use the broader reasoning_sources for context assembly.
    reasoning_chunks = [
        c
        for c in expanded
        if (
            c.get("relative_path", ""),
            c.get("symbol_name", ""),
            int(c.get("start_line", 0)),
            int(c.get("end_line", 0)),
            c.get("expansion_type", "primary"),
        )
        in {
            (
                s.get("relative_path", ""),
                s.get("symbol_name", ""),
                int(s.get("start_line", 0)),
                int(s.get("end_line", 0)),
                s.get("expansion_type", "primary"),
            )
            for s in reasoning_sources
        }
    ]
    reasoning_context, _, reasoning_token_count = assemble_for_reasoning(
        reasoning_chunks or (llm_chunks or expanded),
        history_block_capped,
        primary_intent=primary_intent,
        raw_query=raw_query,
        query_entities=query_info.get("entities"),
    )
    if is_code_request(raw_query):
        started = time.perf_counter()
        # Weak evidence: skip deterministic code mode, fall through to LLM
        if evidence_confidence["level"] == "weak":
            log_event(
                "retrieval.code_answer.skipped", rid,
                reason="weak_evidence", count=evidence_confidence["count"]
            )
        else:
            answer = build_code_answer(raw_query, shown_sources, expanded)
            metrics.add_stage("code_answer", started)
            cited_entities = extract_cited_entities(shown_sources)
            memory.add(
                raw_query, answer,
                resolved_query=_resolved_query_text(query_info, raw_query),
                entities=cited_entities,
                primary_intent=primary_intent,
            )
            meta.update(
                {
                    "stage_latency_ms": metrics.stage_latency_ms,
                    "total_latency_ms": metrics.total_ms(),
                    "backend_latency_ms": metrics.total_ms(),
                    "provider_latency_ms": 0,
                    "errors": metrics.errors,
                    "response_mode": "code_excerpt",
                    "evidence_confidence": evidence_confidence,
                }
            )
            log_event(
                "retrieval.request.end",
                rid,
                status="ok",
                stage_latency_ms=metrics.stage_latency_ms,
                total_latency_ms=metrics.total_ms(),
                candidates=len(candidates),
                expanded=len(expanded),
                shown_sources=len(shown_sources),
                source_filter=meta["source_filter"],
                response_mode="code_excerpt",
                evidence_confidence=evidence_confidence["level"],
            )
            if return_meta:
                return answer, shown_sources, token_count, meta
            return answer, shown_sources, token_count
    if is_architecture_request(raw_query):
        answer = build_architecture_answer(raw_query, shown_sources, expanded)
        cited_entities = extract_cited_entities(shown_sources)
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta.update(
            {
                "stage_latency_ms": metrics.stage_latency_ms,
                "total_latency_ms": metrics.total_ms(),
                "backend_latency_ms": metrics.total_ms(),
                "provider_latency_ms": 0,
                "errors": metrics.errors,
                "response_mode": "architecture_summary",
            }
        )
        log_event(
            "retrieval.request.end",
            rid,
            status="ok",
            stage_latency_ms=metrics.stage_latency_ms,
            total_latency_ms=metrics.total_ms(),
            candidates=len(candidates),
            expanded=len(expanded),
            shown_sources=len(shown_sources),
            source_filter=meta["source_filter"],
            response_mode="architecture_summary",
        )
        if return_meta:
            return answer, shown_sources, token_count, meta
        return answer, shown_sources, token_count
    if is_overview_request(raw_query):
        answer = build_overview_answer(raw_query, shown_sources, expanded)
        cited_entities = extract_cited_entities(shown_sources)
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta.update(
            {
                "stage_latency_ms": metrics.stage_latency_ms,
                "total_latency_ms": metrics.total_ms(),
                "backend_latency_ms": metrics.total_ms(),
                "provider_latency_ms": 0,
                "errors": metrics.errors,
                "response_mode": "overview_summary",
            }
        )
        log_event(
            "retrieval.request.end",
            rid,
            status="ok",
            stage_latency_ms=metrics.stage_latency_ms,
            total_latency_ms=metrics.total_ms(),
            candidates=len(candidates),
            expanded=len(expanded),
            shown_sources=len(shown_sources),
            source_filter=meta["source_filter"],
            response_mode="overview_summary",
        )
        if return_meta:
            return answer, shown_sources, token_count, meta
        return answer, shown_sources, token_count
    if is_flow_explanation_request(raw_query):
        answer, flow_sources = build_flow_answer(
            raw_query,
            shown_sources,
            expanded,
            return_sources=True,
        )
        if flow_sources:
            shown_sources = flow_sources
        cited_entities = extract_cited_entities(shown_sources)
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta.update(
            {
                "stage_latency_ms": metrics.stage_latency_ms,
                "total_latency_ms": metrics.total_ms(),
                "backend_latency_ms": metrics.total_ms(),
                "provider_latency_ms": 0,
                "errors": metrics.errors,
                "response_mode": "flow_summary",
            }
        )
        log_event(
            "retrieval.request.end",
            rid,
            status="ok",
            stage_latency_ms=metrics.stage_latency_ms,
            total_latency_ms=metrics.total_ms(),
            candidates=len(candidates),
            expanded=len(expanded),
            shown_sources=len(shown_sources),
            source_filter=meta["source_filter"],
            response_mode="flow_summary",
        )
        if return_meta:
            return answer, shown_sources, token_count, meta
        return answer, shown_sources, token_count
    # Phase 3: single-symbol deep-dive — runs before generic explanation
    if is_symbol_deep_dive_request(raw_query) and evidence_confidence["level"] != "weak":
        started = time.perf_counter()
        deep_dive_answer = build_symbol_deep_dive_answer(
            raw_query, shown_sources, expanded
        )
        metrics.add_stage("symbol_deep_dive", started)
        if deep_dive_answer:
            cited_entities = extract_cited_entities(shown_sources)
            memory.add(
                raw_query, deep_dive_answer,
                resolved_query=_resolved_query_text(query_info, raw_query),
                entities=cited_entities,
                primary_intent=primary_intent,
            )
            meta.update(
                {
                    "stage_latency_ms": metrics.stage_latency_ms,
                    "total_latency_ms": metrics.total_ms(),
                    "backend_latency_ms": metrics.total_ms(),
                    "provider_latency_ms": 0,
                    "errors": metrics.errors,
                    "response_mode": "symbol_deep_dive",
                    "evidence_confidence": evidence_confidence,
                }
            )
            log_event(
                "retrieval.request.end",
                rid,
                status="ok",
                stage_latency_ms=metrics.stage_latency_ms,
                total_latency_ms=metrics.total_ms(),
                candidates=len(candidates),
                expanded=len(expanded),
                shown_sources=len(shown_sources),
                source_filter=meta["source_filter"],
                response_mode="symbol_deep_dive",
                evidence_confidence=evidence_confidence["level"],
            )
            if return_meta:
                return deep_dive_answer, shown_sources, token_count, meta
            return deep_dive_answer, shown_sources, token_count
        # Empty result: fall through to explanation or LLM
    if is_explanation_request(raw_query):
        # Weak evidence: let LLM handle instead of a thin deterministic explanation
        if evidence_confidence["level"] != "weak":
            answer = build_explanation_answer(raw_query, shown_sources, expanded)
            cited_entities = extract_cited_entities(shown_sources)
            memory.add(
                raw_query, answer,
                resolved_query=_resolved_query_text(query_info, raw_query),
                entities=cited_entities,
                primary_intent=primary_intent,
            )
            meta.update(
                {
                    "stage_latency_ms": metrics.stage_latency_ms,
                    "total_latency_ms": metrics.total_ms(),
                    "backend_latency_ms": metrics.total_ms(),
                    "provider_latency_ms": 0,
                    "errors": metrics.errors,
                    "response_mode": "explanation_summary",
                    "evidence_confidence": evidence_confidence,
                }
            )
            log_event(
                "retrieval.request.end",
                rid,
                status="ok",
                stage_latency_ms=metrics.stage_latency_ms,
                total_latency_ms=metrics.total_ms(),
                candidates=len(candidates),
                expanded=len(expanded),
                shown_sources=len(shown_sources),
                source_filter=meta["source_filter"],
                response_mode="explanation_summary",
                evidence_confidence=evidence_confidence["level"],
            )
            if return_meta:
                return answer, shown_sources, token_count, meta
            return answer, shown_sources, token_count
        log_event(
            "retrieval.explanation.skipped", rid,
            reason="weak_evidence", count=evidence_confidence["count"]
        )
    response_sources = list(shown_sources)
    extra_context_blocks: list[str] = []
    if not is_code_request(raw_query):
        supports = find_supporting_import_exports(
            raw_query,
            response_sources,
            expanded,
            limit=2,
        )
        for support in supports:
            support_source = {
                "relative_path": support["relative_path"],
                "symbol_name": support["symbol_name"],
                "start_line": int(support["start_line"]),
                "end_line": int(support["end_line"]),
                "expansion_type": "supporting_import",
            }
            already_present = any(
                (
                    src.get("relative_path", ""),
                    src.get("symbol_name", ""),
                    int(src.get("start_line", 0)),
                    int(src.get("end_line", 0)),
                )
                == (
                    support_source["relative_path"],
                    support_source["symbol_name"],
                    support_source["start_line"],
                    support_source["end_line"],
                )
                for src in response_sources
            )
            if not already_present:
                response_sources.append(support_source)
            extra_context_blocks.append(str(support["context_block"]))
    llm_backend_started_ms = metrics.total_ms()
    started = time.perf_counter()
    answer = generate_answer(
        raw_query,
        reasoning_context,          # broader context for synthesis
        history_block,
        allowed_sources=response_sources,  # display_sources — strict citation list
        extra_context_blocks=extra_context_blocks,
        provider_config=provider_config,
    )
    token_count = reasoning_token_count
    metrics.add_stage("llm", started)
    # Prepend evidence-quality banner when confidence is weak or partial.
    conf_level = evidence_confidence["level"]
    if conf_level == "weak":
        answer = WEAK_EVIDENCE_BANNER + answer
    elif conf_level == "partial":
        answer = PARTIAL_EVIDENCE_BANNER + answer
    cited_entities = extract_cited_entities(response_sources)
    memory.add(
        raw_query, answer,
        resolved_query=_resolved_query_text(query_info, raw_query),
        entities=cited_entities,
        primary_intent=primary_intent,
    )
    meta.update(
        {
            "stage_latency_ms": metrics.stage_latency_ms,
            "total_latency_ms": metrics.total_ms(),
            "backend_latency_ms": max(0, metrics.total_ms() - metrics.stage_latency_ms.get("llm", 0)),
            "provider_latency_ms": metrics.stage_latency_ms.get("llm", 0),
            "backend_latency_before_llm_ms": llm_backend_started_ms,
            "errors": metrics.errors,
            "response_mode": "llm",
            "evidence_confidence": evidence_confidence,
        }
    )
    log_event(
        "retrieval.request.end",
        rid,
        status="ok",
        stage_latency_ms=metrics.stage_latency_ms,
        total_latency_ms=metrics.total_ms(),
        candidates=len(candidates),
        expanded=len(expanded),
        shown_sources=len(shown_sources),
        display_sources=len(display_sources),
        reasoning_sources=len(reasoning_sources),
        evidence_confidence=evidence_confidence["level"],
        source_filter=meta["source_filter"],
    )
    if return_meta:
        return answer, response_sources, token_count, meta
    return answer, response_sources, token_count


def _resolve_query_info(
    raw_query: str,
    memory: ConversationMemory,
    recent_turns: list[dict] | None = None,
) -> dict:
    """Classify and potentially rewrite the query using recent entity context.

    WS7: entity-aware rewriting. Loads recent cited entities from memory,
    detects topic shifts, and produces a resolved query that replaces vague
    pronoun references with concrete entity names before retrieval.
    """
    query_info = process_query(raw_query)
    recent_turns = recent_turns or []

    # --- Topic-shift detection (WS7) ---
    recent_entity_set = build_recent_entity_set(recent_turns, max_turns=8)
    topic_shift = detect_topic_shift(
        raw_query,
        query_info.get("entities", {}),
        recent_turns,
    )
    query_info["topic_shift"] = topic_shift

    # If topic shift detected, skip follow-up rewriting so old entities
    # don't pollute a genuinely new question.
    if topic_shift:
        return query_info

    if not _should_rewrite_follow_up(raw_query, query_info, memory):
        return query_info

    previous_query = memory.latest_query().strip()
    previous_resolved_query = memory.latest_resolved_query().strip()
    if not previous_query:
        return query_info

    anchor_query = previous_resolved_query or previous_query

    # --- Entity-aware rewrite (WS7) ---
    # Inject recent entity names when the query is vague/pronoun-only.
    rewritten = rewrite_follow_up_query(
        raw_query,
        recent_entity_set,
        previous_resolved_query=anchor_query,
    )
    combined_info = process_query(rewritten)
    combined_info["follow_up_to"] = previous_query
    combined_info["follow_up_resolved_to"] = anchor_query
    combined_info["user_query"] = raw_query.strip()
    combined_info["topic_shift"] = False
    # Preserve entity injection from the original query's exact-term extraction
    # so symbols/files found by process_query on raw_query are not lost.
    original_entities = query_info.get("entities", {})
    merged = combined_info.get("entities", {})
    for key in ("symbols", "files", "env_keys", "routes", "services"):
        existing = merged.get(key, []) or []
        added = original_entities.get(key, []) or []
        merged[key] = _merge_entity_lists(existing, added)
    combined_info["entities"] = merged
    return combined_info


def _should_rewrite_follow_up(
    raw_query: str, query_info: dict, memory: ConversationMemory
) -> bool:
    if not memory.turns:
        return False

    entities = query_info.get("entities", {})
    if entities.get("symbols") or entities.get("files"):
        return False

    lowered = raw_query.strip().lower()
    if not lowered:
        return False

    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", lowered)
    if len(tokens) <= 4:
        return True

    return any(token in FOLLOW_UP_MARKERS for token in tokens)


def _merge_entity_lists(base: list[str], extra: list[str]) -> list[str]:
    """Merge two entity lists, deduplicating while preserving order."""
    seen = set(base)
    merged = list(base)
    for item in extra:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def _resolved_query_text(query_info: dict, raw_query: str) -> str:
    return str(query_info.get("raw_query") or raw_query).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local retrieval pipeline")
    parser.add_argument("--query", help="Single query mode", default="")
    parser.add_argument("--repo-root", help="Repository root used for context reads", default="")
    parser.add_argument("--collection", help="Qdrant collection name", default="")
    args = parser.parse_args()
    if args.repo_root:
        os.environ["RETRIEVAL_REPO_ROOT"] = args.repo_root
    if args.collection:
        os.environ["QDRANT_COLLECTION_NAME"] = args.collection
    validate_collection_binding(get_collection_name(), get_repo_root())

    memory = ConversationMemory(max_turns=CONVERSATION_HISTORY_TURNS)

    if args.query:
        answer, sources, token_count = run_query(args.query, memory)
        _print_result(args.query, answer, sources, token_count)
        return

    print("Codeseek retrieval ready. Type your question or 'exit'.")
    print(f"Repository root: {get_repo_root()}")
    print(f"Collection: {get_collection_name()}")
    print()
    while True:
        try:
            raw_query = input(">>> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not raw_query:
            continue
        if raw_query.lower() in {"exit", "quit"}:
            break

        answer, sources, token_count = run_query(raw_query, memory)
        _print_result(raw_query, answer, sources, token_count)


def _print_result(raw_query: str, answer: str, sources: list[dict], token_count: int) -> None:
    print()
    print(answer)
    print()
    print("Sources:")
    for src in sources:
        label = src["expansion_type"]
        suffix = "" if label == "primary" else f" [{label}]"
        print(
            f"  {src['relative_path']} :: {src['symbol_name']} "
            f"(lines {src['start_line']}-{src['end_line']}){suffix}"
        )
    print(f"[context tokens: {token_count} / {MAX_CONTEXT_TOKENS}]")
    print()


if __name__ == "__main__":
    main()
