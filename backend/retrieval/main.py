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
    build_source_location_answer,
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
    has_strong_source_location_evidence,
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
    "its",
    "that",
    "those",
    "this",
    "these",
    "they",
    "them",
    "there",
    "then",
    "above",
    "previous",
    "continue",
}

LOW_CONTEXT_FALLBACK = (
    "I could not find strong evidence for that in the indexed repository context.\n\n"
    "Try asking with:\n"
    "- a file name\n"
    "- a function name\n"
    "- a feature name"
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


def _write_trace_for_query(
    raw_query: str,
    answer: str,
    response_sources: list[dict],
    expanded: list[dict],
    memory: object,
    metrics: object,
    primary_intent: str | None,
    query_info: dict | None,
    llm_selection: dict | None = None,
) -> None:
    from retrieval.config import ENABLE_ANSWER_TRACE_LOGGING, get_collection_name, get_repo_root
    if not ENABLE_ANSWER_TRACE_LOGGING:
        return

    try:
        from evals.answer_trace_writer import build_answer_trace, write_answer_trace
        session_id = getattr(memory, "session_id", None)
        commit_hash = None
        if session_id:
            try:
                from retrieval.session_indexer import get_session
                session_ = get_session(session_id)
                if session_:
                    commit_hash = session_.get("last_indexed_commit")
            except Exception:
                pass

        used_keys = {
            (
                s.get("relative_path", ""),
                s.get("symbol_name", ""),
                int(s.get("start_line", 0)),
                int(s.get("end_line", 0)),
            )
            for s in response_sources
        }
        retrieved_chunks = [
            c for c in expanded
            if (
                c.get("relative_path", ""),
                c.get("symbol_name", ""),
                int(c.get("start_line", 0)),
                int(c.get("end_line", 0)),
            ) in used_keys
        ]

        trace = build_answer_trace(
            question=raw_query,
            answer=answer,
            retrieved_chunks=retrieved_chunks,
            session_id=session_id,
            collection=get_collection_name(),
            repo_root=get_repo_root(),
            commit_hash=commit_hash,
            provider=llm_selection.get("provider") if llm_selection else None,
            model=llm_selection.get("model") if llm_selection else None,
            reranker_intent=primary_intent,
            label_intent=query_info.get("label_intent") if query_info else None,
            latency_ms=int(metrics.total_ms()) if metrics else None,
            route="retrieval_query",
            extra={
                "top_k": len(response_sources),
                "conversation_id": getattr(memory, "thread_id", None),
                "is_followup": query_info.get("is_followup", False) if query_info else False,
                "is_low_context": query_info.get("is_low_context", False) if query_info else False,
            },
        )
        write_answer_trace(trace)
    except Exception as exc:
        import logging
        logging.warning(f"Failed to write answer trace: {exc}")


def run_query(
    raw_query: str,
    memory: ConversationMemory,
    request_id: str | None = None,
    return_meta: bool = False,
    provider_config: dict | None = None,
    capture_eval: bool = False,
) -> tuple[str, list[dict], int] | tuple[str, list[dict], int, dict]:
    """Run one retrieval query end-to-end."""
    rid = request_id or new_request_id()
    metrics = StageMetrics(request_id=rid)
    meta: dict = {"request_id": rid}
    evaluation = meta.setdefault("evaluation", {}) if capture_eval else None
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
    assemble_result = assemble(
        expanded,
        history_block_capped,
        primary_intent=primary_intent,
        raw_query=raw_query,
        return_blocks=capture_eval,
    )
    if len(assemble_result) == 4:
        context, sources, token_count, context_blocks = assemble_result
    else:
        context, sources, token_count = assemble_result
        context_blocks = []
    metrics.add_stage("assemble", started)
    if evaluation is not None:
        evaluation["query_info"] = query_info
        evaluation["search_candidates"] = list(candidates)
        evaluation["expanded_candidates"] = list(expanded)
        evaluation["assembled_context"] = context
        evaluation["assembled_context_blocks"] = list(context_blocks)
        evaluation["assembled_sources"] = list(sources)
        evaluation["deterministic_context_token_count"] = int(token_count)
    meta["source_filter"] = explain_source_filter_decision(raw_query, sources)
    # Two-layer source gating: display_sources for citations, reasoning_sources for context.
    display_sources, reasoning_sources = split_sources_two_layer(
        raw_query, sources, enabled=ENABLE_TWO_LAYER_SOURCES
    )
    shown_sources = display_sources
    evidence_confidence = score_evidence_confidence(raw_query, shown_sources, query_info=query_info)
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
        if evaluation is not None:
            evaluation["response_mode"] = "low_context"
            evaluation["display_sources"] = list(shown_sources)
            evaluation["reasoning_sources"] = list(reasoning_sources)
            evaluation["answer_context"] = ""
            evaluation["answer_context_blocks"] = []
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
        _write_trace_for_query(
            raw_query=raw_query,
            answer=answer,
            response_sources=shown_sources,
            expanded=expanded,
            memory=memory,
            metrics=metrics,
            primary_intent=primary_intent,
            query_info=query_info,
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
        llm_assemble_result = assemble(
            llm_chunks,
            history_block_capped,
            primary_intent=primary_intent,
            raw_query=raw_query,
            return_blocks=capture_eval,
        )
        if len(llm_assemble_result) == 4:
            context, _, token_count, context_blocks = llm_assemble_result
        else:
            context, _, token_count = llm_assemble_result
            context_blocks = []
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
    reasoning_assemble_result = assemble_for_reasoning(
        reasoning_chunks or (llm_chunks or expanded),
        history_block_capped,
        primary_intent=primary_intent,
        raw_query=raw_query,
        query_entities=query_info.get("entities"),
        return_blocks=capture_eval,
    )
    if len(reasoning_assemble_result) == 4:
        reasoning_context, _, reasoning_token_count, reasoning_context_blocks = reasoning_assemble_result
    else:
        reasoning_context, _, reasoning_token_count = reasoning_assemble_result
        reasoning_context_blocks = []
    if evaluation is not None:
        evaluation["display_sources"] = list(display_sources)
        evaluation["reasoning_sources"] = list(reasoning_sources)
        evaluation["deterministic_context"] = context
        evaluation["deterministic_context_blocks"] = list(context_blocks)
        evaluation["reasoning_context"] = reasoning_context
        evaluation["reasoning_context_blocks"] = list(reasoning_context_blocks)
        evaluation["reasoning_context_token_count"] = int(reasoning_token_count)
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
            if evaluation is not None:
                evaluation["response_mode"] = "code_excerpt"
                evaluation["answer_context"] = context
                evaluation["answer_context_blocks"] = list(context_blocks)
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
            _write_trace_for_query(
                raw_query=raw_query,
                answer=answer,
                response_sources=shown_sources,
                expanded=expanded,
                memory=memory,
                metrics=metrics,
                primary_intent=primary_intent,
                query_info=query_info,
            )
            if return_meta:
                return answer, shown_sources, token_count, meta
            return answer, shown_sources, token_count
    if is_architecture_request(raw_query):
        answer, architecture_sources = build_architecture_answer(
            raw_query,
            shown_sources,
            expanded,
            return_sources=True,
        )
        if architecture_sources:
            shown_sources = architecture_sources
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
        if evaluation is not None:
            evaluation["response_mode"] = "architecture_summary"
            evaluation["answer_context"] = context
            evaluation["answer_context_blocks"] = list(context_blocks)
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
        _write_trace_for_query(
            raw_query=raw_query,
            answer=answer,
            response_sources=shown_sources,
            expanded=expanded,
            memory=memory,
            metrics=metrics,
            primary_intent=primary_intent,
            query_info=query_info,
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
        if evaluation is not None:
            evaluation["response_mode"] = "overview_summary"
            evaluation["answer_context"] = context
            evaluation["answer_context_blocks"] = list(context_blocks)
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
        _write_trace_for_query(
            raw_query=raw_query,
            answer=answer,
            response_sources=shown_sources,
            expanded=expanded,
            memory=memory,
            metrics=metrics,
            primary_intent=primary_intent,
            query_info=query_info,
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
        if evaluation is not None:
            evaluation["response_mode"] = "flow_summary"
            evaluation["answer_context"] = context
            evaluation["answer_context_blocks"] = list(context_blocks)
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
        _write_trace_for_query(
            raw_query=raw_query,
            answer=answer,
            response_sources=shown_sources,
            expanded=expanded,
            memory=memory,
            metrics=metrics,
            primary_intent=primary_intent,
            query_info=query_info,
        )
        if return_meta:
            return answer, shown_sources, token_count, meta
        return answer, shown_sources, token_count

    # Phase 2.5: source-location queries with strong evidence
    if has_strong_source_location_evidence(raw_query, shown_sources, query_info):
        started = time.perf_counter()
        answer = build_source_location_answer(raw_query, shown_sources, query_info)
        metrics.add_stage("source_location_answer", started)
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
                "response_mode": "source_location",
                "evidence_confidence": evidence_confidence,
            }
        )
        if evaluation is not None:
            evaluation["response_mode"] = "source_location"
            evaluation["answer_context"] = context
            evaluation["answer_context_blocks"] = list(context_blocks)
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
            response_mode="source_location",
            evidence_confidence=evidence_confidence["level"],
        )
        _write_trace_for_query(
            raw_query=raw_query,
            answer=answer,
            response_sources=shown_sources,
            expanded=expanded,
            memory=memory,
            metrics=metrics,
            primary_intent=primary_intent,
            query_info=query_info,
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
            if evaluation is not None:
                evaluation["response_mode"] = "symbol_deep_dive"
                evaluation["answer_context"] = context
                evaluation["answer_context_blocks"] = list(context_blocks)
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
            _write_trace_for_query(
                raw_query=raw_query,
                answer=deep_dive_answer,
                response_sources=shown_sources,
                expanded=expanded,
                memory=memory,
                metrics=metrics,
                primary_intent=primary_intent,
                query_info=query_info,
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
            if evaluation is not None:
                evaluation["response_mode"] = "explanation_summary"
                evaluation["answer_context"] = context
                evaluation["answer_context_blocks"] = list(context_blocks)
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
            _write_trace_for_query(
                raw_query=raw_query,
                answer=answer,
                response_sources=shown_sources,
                expanded=expanded,
                memory=memory,
                metrics=metrics,
                primary_intent=primary_intent,
                query_info=query_info,
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
    support_blocks: list[dict] = []
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
            support_blocks.append(
                {
                    "block_type": "supporting_import",
                    "text": str(support["context_block"]),
                    "relative_path": support["relative_path"],
                    "symbol_name": support["symbol_name"],
                    "start_line": int(support["start_line"]),
                    "end_line": int(support["end_line"]),
                    "support_kind": support.get("support_kind", ""),
                }
            )
    llm_backend_started_ms = metrics.total_ms()
    started = time.perf_counter()
    llm_selection: dict[str, object] = {}
    answer = generate_answer(
        raw_query,
        reasoning_context,          # broader context for synthesis
        history_block,
        allowed_sources=response_sources,  # display_sources — strict citation list
        extra_context_blocks=extra_context_blocks,
        provider_config=provider_config,
        query_info=query_info,
        evidence_confidence=evidence_confidence,
        selection_meta=llm_selection,
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
            "llm_selection": llm_selection,
        }
    )
    if evaluation is not None:
        evaluation["response_mode"] = "llm"
        evaluation["answer_context"] = reasoning_context
        evaluation["answer_context_blocks"] = list(reasoning_context_blocks) + support_blocks
        evaluation["support_blocks"] = list(support_blocks)
        evaluation["llm_selection"] = dict(llm_selection)
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
        llm_provider=llm_selection.get("provider", ""),
        llm_model=llm_selection.get("model", ""),
        llm_routing_mode=llm_selection.get("routing_mode", ""),
        source_filter=meta["source_filter"],
    )
    _write_trace_for_query(
        raw_query=raw_query,
        answer=answer,
        response_sources=response_sources,
        expanded=expanded,
        memory=memory,
        metrics=metrics,
        primary_intent=primary_intent,
        query_info=query_info,
        llm_selection=llm_selection,
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

    # Calculate is_followup and is_low_context using state
    from retrieval.query_intent import identify_followup_or_low_context
    conversation_state = {
        "previous_files": recent_entity_set.get("files", []),
        "previous_symbols": recent_entity_set.get("symbols", []),
        "previous_query": memory.latest_query()
    }
    is_followup_detected, is_low_context_detected = identify_followup_or_low_context(raw_query, conversation_state)

    query_info["is_followup"] = bool(is_followup_detected and not topic_shift)
    query_info["conversation_state"] = conversation_state
    if is_low_context_detected:
        query_info["primary_intent"] = "LOW_CONTEXT"

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
    combined_info["is_followup"] = bool(is_followup_detected and not topic_shift)
    if is_low_context_detected:
        combined_info["primary_intent"] = "LOW_CONTEXT"

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
    combined_info["conversation_state"] = conversation_state
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
