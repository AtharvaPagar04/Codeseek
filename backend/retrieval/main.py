"""Entry point for retrieval pipeline."""

import argparse
import os
import re
import time

from retrieval.assembler import assemble, assemble_for_reasoning, intent_history_cap
from retrieval.code_answers import (
    build_architecture_answer,
    build_docs_summary_answer,
    build_explanation_answer,
    build_code_answer,
    build_code_snippet_answer,
    collect_rendered_code_snippet_sources,
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
    filesystem_exact_symbol_sources_for_query,
    preferred_docs_summary_sources,
    route_filesystem_sources_for_query,
    rank_follow_up_sources_for_explanation,
)
from retrieval.answer_validation import validate_generated_answer
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
    is_vague_follow_up_query,
    latest_rendered_entity_set,
    rewrite_follow_up_query,
)
from retrieval.llm import generate_answer
from retrieval.memory import ConversationMemory
from retrieval.observability import StageMetrics, log_event, new_request_id
from retrieval.query_processor import process_query
from retrieval.isolation import validate_collection_binding
from retrieval.query_intent import is_source_location_query
from retrieval.searcher import search
from retrieval.searcher import query_explicitly_requests_non_implementation_artifacts
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
    if hasattr(memory, "last_answer") and getattr(memory, "last_answer") is not None:
        answer = getattr(memory, "last_answer")
    if hasattr(memory, "last_sources") and getattr(memory, "last_sources") is not None:
        response_sources = getattr(memory, "last_sources")

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


class PostProcessingMemoryProxy:
    def __init__(self, target_memory, raw_query):
        self._target = target_memory
        self._raw_query = raw_query
        self.last_answer = None
        self.last_sources = None

    def add(self, query, answer, resolved_query=None, *, entities=None, primary_intent=""):
        import sys
        # 1. Dynamically retrieve shown_sources or response_sources from caller's local scope
        caller_frame = sys._getframe(1)
        caller_locals = caller_frame.f_locals
        sources = caller_locals.get("response_sources") or caller_locals.get("shown_sources") or []
        response_mode = caller_locals.get("response_mode") or caller_locals.get("meta", {}).get("response_mode", "")
        query_info = caller_locals.get("query_info")

        # 2. Apply post-processing
        post_processed_ans, final_sources = post_process_answer_and_sources(answer, sources, self._raw_query, primary_intent=primary_intent)

        validation = validate_generated_answer(
            answer=post_processed_ans,
            raw_query=self._raw_query,
            response_mode=str(response_mode or ""),
            allowed_sources=list(sources),
            final_sources=list(final_sources),
            query_info=query_info if isinstance(query_info, dict) else None,
        )
        self.last_validation = validation
        post_processed_ans = validation.get("repaired_answer") or post_processed_ans
        repaired_sources = validation.get("repaired_sources")
        if repaired_sources is not None:
            final_sources = repaired_sources

        self.last_answer = post_processed_ans
        self.last_sources = final_sources

        # 3. Re-calculate entities using the final pruned sources
        from retrieval.follow_up_memory import extract_cited_entities
        new_entities = extract_cited_entities(final_sources)

        # 4. Save to target memory/database
        self._target.add(
            query=query,
            answer=post_processed_ans,
            resolved_query=resolved_query,
            entities=new_entities,
            rendered_sources=final_sources,
            primary_intent=primary_intent,
        )

    def __getattr__(self, name):
        return getattr(self._target, name)


def post_process_answer_and_sources(
    answer: str,
    sources: list[dict],
    raw_query: str,
    primary_intent: str | None = None,
) -> tuple[str, list[dict]]:
    import re
    from retrieval.searcher import (
        match_code_topic_route,
        path_matches_topic_route,
        query_explicitly_requests_non_implementation_artifacts,
        query_explicitly_requests_searcher_internals,
        symbol_matches_topic_route,
    )
    from retrieval.code_answers import route_filesystem_sources_for_query
    from retrieval.source_filter import apply_query_negative_filters

    def _dedupe_sources(items: list[dict]) -> list[dict]:
        seen = set()
        deduped = []
        for src in items:
            rel_path = src.get("relative_path", "")
            symbol_name = str(src.get("symbol_name", "")).strip()
            start_line = int(src.get("start_line", 0) or 0)
            end_line = int(src.get("end_line", 0) or 0)
            if start_line > 0 and end_line > 0:
                key = (rel_path, symbol_name, start_line, end_line)
            else:
                key = (rel_path, symbol_name)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(src)
        return deduped

    def _filter_lines_outside_code_blocks(
        text: str,
        *,
        keep_line=None,
        drop_line=None,
    ) -> str:
        cleaned: list[str] = []
        in_code_block = False
        for line in text.splitlines():
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                cleaned.append(line)
                continue
            if in_code_block:
                cleaned.append(line)
                continue
            if keep_line is not None and not keep_line(line):
                continue
            if drop_line is not None and drop_line(line):
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    # 1. Strip internal/debug phrases
    internals = [
        "direct injected candidate",
        "direct injected file candidate",
        "reranker boost",
        "source role classifier",
        "exact retrieval hit",
        "internal score"
    ]
    for term in internals:
        # Match case-insensitively, optionally followed by bracketed text or paths
        answer = re.sub(r"\b" + re.escape(term) + r"\b", "", answer, flags=re.IGNORECASE)
        # Also clean up any lingering mentions with paths (like Direct injected file candidate backend/...)
        answer = re.sub(re.escape(term) + r"\s+\S+", "", answer, flags=re.IGNORECASE)

    # Clean double spaces/newlines while preserving leading indentation on each line
    answer = re.sub(r" +\n", "\n", answer)  # Remove trailing spaces on lines
    
    # Split the string by lines, collapse multiple spaces inside each line
    # but PRESERVE the leading whitespace of each line!
    lines = answer.splitlines()
    for i, line in enumerate(lines):
        # Find leading whitespace
        leading_ws = re.match(r"^(\s*)", line).group(1)
        content = line[len(leading_ws):]
        # Collapse multiple spaces in content
        content = re.sub(r" {2,}", " ", content)
        lines[i] = leading_ws + content
    answer = "\n".join(lines)

    # 1.5. Flow summary formatting post-processing
    if "The flow appears to be:" in answer or "Evidence status:" in answer:
        # Match lines starting with optional whitespace followed by * file: or * role:
        # We ensure they are indented by exactly 3 spaces.
        # We also ensure the file path is wrapped in backticks if it is not already.
        def wrap_file_path(match):
            path_val = match.group(1).strip()
            # If already wrapped in backticks, return as is
            if path_val.startswith("`") and path_val.endswith("`"):
                return f"   * file: {path_val}"
            # Otherwise wrap it
            return f"   * file: `{path_val}`"
            
        answer = re.sub(r"(?m)^\s*\*?\s*file:\s*(.*)", wrap_file_path, answer)
        answer = re.sub(r"(?m)^\s*\*?\s*role:\s*(.*)", r"   * role: \1", answer)

    # 2. Fix evidence status contradiction
    # If the answer contains "Evidence status:"
    if "Evidence status:" in answer:
        has_missing = "missing:" in answer
        if has_missing:
            # Replace complete with partial in a case-insensitive manner
            answer = re.sub(r"([*|-]\s+)complete", r"\1partial", answer, flags=re.IGNORECASE)

    # 3. Remove docs/tests/scratch from visible source list when implementation sources are complete.
    implementation_sources = []
    for src in sources:
        path = (src.get("relative_path") or "").lower()
        is_impl = (
            path.endswith(".py") or path.endswith(".js") or path.endswith(".jsx") or path.endswith(".ts") or path.endswith(".tsx")
        ) and not (
            "test" in path or "scratch" in path or "benchmark" in path or "docs/" in path or path.endswith(".md") or "/reports/" in path
        )
        if is_impl:
            implementation_sources.append(src)

    q_lower = raw_query.lower()
    explicit_request = (
        "repo_freshness_report.md" in q_lower
        or query_explicitly_requests_non_implementation_artifacts(raw_query)
        or "overview" in q_lower
        or "architecture" in q_lower
        or "structure" in q_lower
        or "codebase" in q_lower
        or "about" in q_lower
        or "project" in q_lower
        or "backend modules" in q_lower
        or "backend subsystems" in q_lower
        or "what does" in q_lower
        or "repo do" in q_lower
        or "skills" in q_lower
        or "skill" in q_lower
    )
    is_flow_query = "flow" in q_lower or "pipeline" in q_lower or "retrieval pipeline" in q_lower

    final_sources = list(sources)
    if implementation_sources and not explicit_request and not is_flow_query:
        final_sources = implementation_sources
        
        # Clean answer lines referencing docs/tests/scratch
        answer = _filter_lines_outside_code_blocks(
            answer,
            drop_line=lambda line: (
                "repo_freshness_report.md" in line.lower()
                or "reports/" in line.lower()
                or "/tests/" in line.lower()
                or "_test.py" in line.lower()
                or "scratch/" in line.lower()
                or "benchmark" in line.lower()
                or line.lower().strip().endswith(".md`:")
                or line.lower().strip().endswith(".md`")
            ),
        )

    # 3.5. Remove query_intent.py from final_sources and clean it from answer (unless explicit)
    query_intent_explicit = any(
        term in raw_query.lower()
        for term in [
            "query_intent.py",
            "is_code_request_query",
            "code request detection",
            "intent classifier",
            "query classification",
        ]
    )
    if not query_intent_explicit:
        final_sources = [
            src for src in final_sources
            if "query_intent.py" not in (src.get("relative_path") or "")
        ]
        # Clean answer lines referencing query_intent.py or is_code_request_query
        answer = _filter_lines_outside_code_blocks(
            answer,
            drop_line=lambda line: "query_intent.py" in line.lower() or "is_code_request_query" in line.lower(),
        )

    matched_code_topic_route = match_code_topic_route(raw_query, primary_intent)
    strict_code_topic_route = bool(
        matched_code_topic_route
        and not query_explicitly_requests_non_implementation_artifacts(raw_query)
        and not query_explicitly_requests_searcher_internals(raw_query)
    )
    if strict_code_topic_route:
        route_id = matched_code_topic_route.get("id")
        filesystem_sources = route_filesystem_sources_for_query(raw_query)
        if route_id in {"safe_eval_runner", "qdrant_upsert", "evaluation_report_api"} and filesystem_sources:
            final_sources = filesystem_sources
        else:
            routed_sources = [
                src
                for src in final_sources
                if (
                    path_matches_topic_route(src.get("relative_path", ""), matched_code_topic_route)
                    or symbol_matches_topic_route(
                        src.get("symbol_name", ""),
                        src.get("relative_path", ""),
                        matched_code_topic_route,
                    )
                )
            ]
            if routed_sources:
                final_sources = routed_sources

    final_sources = _dedupe_sources(final_sources)
    final_sources = apply_query_negative_filters(
        final_sources,
        raw_query,
        intent=primary_intent,
        matched_route=matched_code_topic_route if strict_code_topic_route else None,
    )

    symbol_source_paths = {
        str(src.get("relative_path", ""))
        for src in final_sources
        if str(src.get("symbol_name", "")).strip() and str(src.get("symbol_name", "")).strip() != "<file>"
    }
    if symbol_source_paths:
        final_sources = [
            src
            for src in final_sources
            if not (
                str(src.get("relative_path", "")) in symbol_source_paths
                and str(src.get("symbol_name", "")).strip() in {"", "<file>"}
            )
        ]
    final_sources = _dedupe_sources(final_sources)

    # 4. Prevent source files not in selected context from appearing in the final answer
    allowed_paths = {src.get("relative_path") for src in final_sources if src.get("relative_path")}
    answer = _filter_lines_outside_code_blocks(
        answer,
        keep_line=lambda line: all(
            any(
                p == path or p.endswith("/" + path) or path.endswith("/" + p)
                for p in allowed_paths
            )
            for path in re.findall(r'`([a-zA-Z0-9_\-/]+\.(?:py|js|jsx|ts|tsx|md))`', line)
        ),
    )

    # 5. Code request post-processing
    from retrieval.query_intent import is_explanation_query
    is_code_req = ((primary_intent == "CODE_REQUEST") or is_code_request(raw_query)) and not is_explanation_query(raw_query)
    if is_code_req:
        # Strip manual Sources footer (case-insensitive)
        sources_match = re.search(r"(?im)^\s*\*?\*?\s*Sources:\s*", answer)
        if sources_match:
            answer = answer[:sources_match.start()]

        # Clean any trailing lines representing files/lists of files
        lines = answer.splitlines()
        while lines:
            last_line = lines[-1].strip()
            if not last_line:
                lines.pop()
            elif (last_line.startswith("*") or last_line.startswith("-") or re.match(r"^\d+\.", last_line)) and (
                "file" in last_line.lower() or "/" in last_line or "." in last_line or "`" in last_line
            ):
                lines.pop()
            else:
                break
        answer = "\n".join(lines)

        # Block bad patterns
        bad_prose = [
            r"(?i)Summary of Authentication Flow",
            r"(?i)This flow ensures",
            r"(?i)\bRole:",
        ]
        for pat in bad_prose:
            answer = re.sub(pat, "", answer)
            
        # Clean double spaces/newlines
        answer = re.sub(r" +\n", "\n", answer)
        
        # Replace old intro if present
        old_intro = "Code snippets from retrieved context:"
        if old_intro in answer:
            from retrieval.query_processor import _extract_symbols
            extracted_symbols = _extract_symbols(raw_query)
            
            is_broad_auth = False
            auth_words = {"auth", "authentication", "session", "cookie", "token"}
            if any(w in raw_query.lower() for w in auth_words):
                target_auth_symbols = [
                    "_auth_key",
                    "_require_auth",
                    "_current_auth_user",
                    "_require_auth_user",
                    "create_auth_session",
                    "get_user_for_session_token",
                    "upsert_github_user",
                    "delete_auth_session"
                ]
                has_specific_auth_symbol = False
                for sym in target_auth_symbols:
                    if re.search(r"\b" + re.escape(sym) + r"\b", raw_query.lower()):
                        has_specific_auth_symbol = True
                        break
                if not has_specific_auth_symbol and extracted_symbols:
                    for sym in extracted_symbols:
                        if sym.lower() not in auth_words:
                            has_specific_auth_symbol = True
                            break
                if not has_specific_auth_symbol:
                    is_broad_auth = True
                    
            if is_broad_auth:
                new_intro = "I found multiple auth-related functions:"
            elif len(extracted_symbols) > 1:
                new_intro = "I found multiple matching code snippets:"
            else:
                new_intro = "Here is the matching function:"
                
            answer = answer.replace(old_intro, new_intro)

        has_fenced_code = "```" in answer
        
        # Let's check code availability
        code_available = False
        from retrieval.code_answers import _read_source_excerpt
        for src in final_sources:
            if src.get("relative_path") and src.get("symbol_name"):
                if _read_source_excerpt(src).strip():
                    code_available = True
                    break
                    
        if not has_fenced_code and final_sources:
            if code_available:
                from retrieval.code_answers import build_code_snippet_answer
                answer = build_code_snippet_answer(raw_query, final_sources, final_sources)
            else:
                answer = "I found a matching function reference, but the function body was not included in the retrieved context."
        
        # Clean prose-only sentences when code is available
        if "```" in answer:
            lines = answer.splitlines()
            cleaned = []
            in_code_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_code_block = not in_code_block
                    cleaned.append(line)
                    continue
                if in_code_block:
                    cleaned.append(line)
                    continue
                    
                # In prose
                line_lower = line.lower()
                is_intro_line = any(
                    intro in line_lower
                    for intro in [
                        "here is the matching function",
                        "i found multiple auth-related functions",
                        "i found multiple matching code snippets"
                    ]
                )
                if "function:" in line_lower and "`" not in line and not is_intro_line:
                    continue
                if any(x in line for x in ["Summary of Authentication Flow", "This flow ensures", "Role:"]):
                    continue
                cleaned.append(line)
            answer = "\n".join(cleaned)
            
        # Ensure all files referenced in the answer are in final_sources
        mentioned_files = re.findall(r'`([a-zA-Z0-9_\-/]+\.(?:py|js|jsx|ts|tsx|md))`', answer)
        mentioned_set = set(mentioned_files)
        if mentioned_set:
            final_sources = [
                src for src in final_sources
                if src.get("relative_path") in mentioned_set
            ]
            for f in mentioned_files:
                if not any(src.get("relative_path") == f for src in final_sources):
                    final_sources.append({
                        "relative_path": f,
                        "symbol_name": "",
                        "chunk_type": "file",
                        "expansion_type": "primary"
                    })
        else:
            final_sources = []

    return answer.strip(), final_sources


def run_query(
    raw_query: str,
    memory: ConversationMemory,
    request_id: str | None = None,
    return_meta: bool = False,
    provider_config: dict | None = None,
    capture_eval: bool = False,
) -> tuple[str, list[dict], int] | tuple[str, list[dict], int, dict]:
    proxy_memory = PostProcessingMemoryProxy(memory, raw_query)
    res = _run_query_impl(
        raw_query=raw_query,
        memory=proxy_memory,
        request_id=request_id,
        return_meta=return_meta,
        provider_config=provider_config,
        capture_eval=capture_eval,
    )
    final_answer = proxy_memory.last_answer if proxy_memory.last_answer is not None else res[0]
    final_sources = proxy_memory.last_sources if proxy_memory.last_sources is not None else res[1]
    
    if return_meta:
        answer, sources, token_count, meta = res
        if "evaluation" in meta:
            meta["evaluation"]["display_sources"] = list(final_sources)
        return final_answer, final_sources, token_count, meta
    else:
        answer, sources, token_count = res
        return final_answer, final_sources, token_count


def _run_query_impl(
    raw_query: str,
    memory: ConversationMemory,
    request_id: str | None = None,
    return_meta: bool = False,
    provider_config: dict | None = None,
    capture_eval: bool = False,
) -> tuple[str, list[dict], int] | tuple[str, list[dict], int, dict]:
    """Run one retrieval query end-to-end (implementation)."""
    rid = request_id or new_request_id()
    metrics = StageMetrics(request_id=rid)
    meta: dict = {"request_id": rid}
    evaluation = meta.setdefault("evaluation", {}) if capture_eval else None
    log_event("retrieval.request.start", rid, query=raw_query)
    validate_collection_binding(get_collection_name(), get_repo_root())
    started = time.perf_counter()
    explicit_non_impl_request = query_explicitly_requests_non_implementation_artifacts(raw_query)
    history_block = memory.get_history_block()  # full, for search/follow-up rewrite
    if explicit_non_impl_request:
        history_block = ""
    metrics.add_stage("history", started)
    started = time.perf_counter()
    # WS7: load recent cited entities and pass them into query resolution.
    recent_turns = memory.recent_turn_entities(max_turns=8) if hasattr(memory, "recent_turn_entities") else []
    query_info = _resolve_query_info(raw_query, memory, recent_turns=recent_turns)
    metrics.add_stage("query_processor", started)
    # Resolve intent early so the history cap can be applied before assembly.
    primary_intent = query_info.get("primary_intent") or query_info.get("intent")
    meta["query_intent"] = str(query_info.get("intent") or "").strip()
    meta["primary_intent"] = str(primary_intent or "").strip()
    history_cap = intent_history_cap(primary_intent)
    history_block_capped = memory.get_history_block_capped(history_cap)
    if explicit_non_impl_request:
        history_block_capped = ""
    started = time.perf_counter()
    candidates = search(query_info)
    metrics.add_stage("search", started)
    started = time.perf_counter()
    expanded = expand(candidates, query_info)
    query_intent_explicit = any(
        term in raw_query.lower()
        for term in [
            "query_intent.py",
            "is_code_request_query",
            "code request detection",
            "intent classifier",
            "query classification",
        ]
    )
    if not query_intent_explicit:
        expanded = [
            c for c in expanded
            if "query_intent.py" not in (c.get("relative_path") or "")
        ]
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
    follow_up_anchor_paths = {
        str(path).lower()
        for path in (query_info.get("follow_up_anchor_paths") or [])
        if str(path).strip()
    }
    if follow_up_anchor_paths and is_vague_follow_up_query(raw_query):
        def _restrict_to_anchor_family(items: list[dict]) -> list[dict]:
            restricted = [
                src for src in items
                if str(src.get("relative_path", "")).lower() in follow_up_anchor_paths
            ]
            return restricted or items

        display_sources = _restrict_to_anchor_family(display_sources)
        reasoning_sources = _restrict_to_anchor_family(reasoning_sources)
        shown_sources = display_sources
    evidence_confidence = score_evidence_confidence(raw_query, shown_sources, query_info=query_info)
    if is_flow_explanation_request(raw_query):
        flow_sources = select_sources_for_display(raw_query, expanded)
        if flow_sources:
            shown_sources = flow_sources
    if explicit_non_impl_request:
        docs_sources = preferred_docs_summary_sources(shown_sources)
        if not docs_sources:
            answer = LOW_CONTEXT_FALLBACK
            cited_entities = {}
            response_mode = "low_context"
            memory.add(
                raw_query, answer,
                resolved_query=_resolved_query_text(query_info, raw_query),
                entities=cited_entities,
                primary_intent=primary_intent,
            )
            meta["validation"] = getattr(memory, "last_validation", None)
            meta.update(
                {
                    "stage_latency_ms": metrics.stage_latency_ms,
                    "total_latency_ms": metrics.total_ms(),
                    "backend_latency_ms": metrics.total_ms(),
                    "provider_latency_ms": 0,
                    "errors": metrics.errors,
                    "response_mode": "low_context",
                    "evidence_confidence": {"level": "weak", "reason": "no docs sources assembled", "count": 0},
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
                fallback="no_docs_sources",
                candidates=len(candidates),
                expanded=len(expanded),
                shown_sources=len(docs_sources),
                source_filter=meta["source_filter"],
                response_mode="low_context",
                evidence_confidence="weak",
            )
            _write_trace_for_query(
                raw_query=raw_query,
                answer=answer,
                response_sources=[],
                expanded=expanded,
                memory=memory,
                metrics=metrics,
                primary_intent=primary_intent,
                query_info=query_info,
            )
            if return_meta:
                return answer, [], token_count, meta
            return answer, [], token_count
        started = time.perf_counter()
        answer = build_docs_summary_answer(raw_query, docs_sources, expanded)
        metrics.add_stage("docs_summary_answer", started)
        cited_entities = extract_cited_entities(docs_sources)
        response_mode = "docs_summary"
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta["validation"] = getattr(memory, "last_validation", None)
        meta.update(
            {
                "stage_latency_ms": metrics.stage_latency_ms,
                "total_latency_ms": metrics.total_ms(),
                "backend_latency_ms": metrics.total_ms(),
                "provider_latency_ms": 0,
                "errors": metrics.errors,
                "response_mode": "docs_summary",
                "evidence_confidence": evidence_confidence,
            }
        )
        if evaluation is not None:
            evaluation["response_mode"] = "docs_summary"
            evaluation["display_sources"] = list(docs_sources)
            evaluation["reasoning_sources"] = list(reasoning_sources)
            evaluation["answer_context"] = ""
            evaluation["answer_context_blocks"] = []
        log_event(
            "retrieval.request.end",
            rid,
            status="ok",
            stage_latency_ms=metrics.stage_latency_ms,
            total_latency_ms=metrics.total_ms(),
            candidates=len(candidates),
            expanded=len(expanded),
            shown_sources=len(docs_sources),
            source_filter=meta["source_filter"],
            response_mode="docs_summary",
            evidence_confidence=evidence_confidence["level"],
        )
        _write_trace_for_query(
            raw_query=raw_query,
            answer=answer,
            response_sources=docs_sources,
            expanded=expanded,
            memory=memory,
            metrics=metrics,
            primary_intent=primary_intent,
            query_info=query_info,
        )
        if return_meta:
            return answer, docs_sources, token_count, meta
        return answer, docs_sources, token_count
    if not shown_sources:
        answer = LOW_CONTEXT_FALLBACK
        cited_entities = {}
        response_mode = "low_context"
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta["validation"] = getattr(memory, "last_validation", None)
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
    meta["display_sources"] = list(display_sources)
    meta["reasoning_sources"] = list(reasoning_sources)
    if is_code_request(raw_query):
        started = time.perf_counter()
        from retrieval.searcher import match_code_topic_route
        matched_code_topic_route = match_code_topic_route(raw_query, primary_intent)
        route_support_sources = route_filesystem_sources_for_query(raw_query) if matched_code_topic_route else []
        exact_symbol_support_sources = filesystem_exact_symbol_sources_for_query(
            raw_query,
            list(shown_sources) + list(expanded),
        )
        rendered_code_sources = collect_rendered_code_snippet_sources(raw_query, list(shown_sources), list(expanded))
        # Weak evidence: skip deterministic code mode, fall through to LLM
        if evidence_confidence["level"] == "weak" and not matched_code_topic_route:
            log_event(
                "retrieval.code_answer.skipped", rid,
                reason="weak_evidence", count=evidence_confidence["count"]
            )
        else:
            answer = build_code_snippet_answer(raw_query, shown_sources, expanded)
            response_mode = "code_snippet"
            if matched_code_topic_route or exact_symbol_support_sources or rendered_code_sources:
                answer, shown_sources = post_process_answer_and_sources(
                    answer,
                    rendered_code_sources or exact_symbol_support_sources or route_support_sources,
                    raw_query,
                    primary_intent=primary_intent,
                )
            metrics.add_stage("code_answer", started)
            cited_entities = extract_cited_entities(shown_sources)
            memory.add(
                raw_query, answer,
                resolved_query=_resolved_query_text(query_info, raw_query),
                entities=cited_entities,
                primary_intent=primary_intent,
            )
            meta["validation"] = getattr(memory, "last_validation", None)
            meta.update(
                {
                    "stage_latency_ms": metrics.stage_latency_ms,
                    "total_latency_ms": metrics.total_ms(),
                    "backend_latency_ms": metrics.total_ms(),
                    "provider_latency_ms": 0,
                    "errors": metrics.errors,
                    "response_mode": "code_snippet",
                    "evidence_confidence": evidence_confidence,
                }
            )
            if evaluation is not None:
                evaluation["response_mode"] = "code_snippet"
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
        response_mode = "architecture_summary"
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta["validation"] = getattr(memory, "last_validation", None)
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
        response_mode = "overview_summary"
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta["validation"] = getattr(memory, "last_validation", None)
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
        response_mode = "flow_summary"
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta["validation"] = getattr(memory, "last_validation", None)
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
    if is_source_location_query(raw_query):
        from retrieval.searcher import match_code_topic_route, path_matches_topic_route

        matched_route = match_code_topic_route(raw_query, primary_intent)
        if matched_route and matched_route.get("id") in {"evaluation_report_api", "retrieval_internals"}:
            route_sources = [
                src for src in shown_sources
                if path_matches_topic_route(src.get("relative_path", ""), matched_route)
            ]
            if not route_sources:
                route_sources = [
                    src for src in expanded
                    if path_matches_topic_route(src.get("relative_path", ""), matched_route)
                ]
            if not route_sources:
                route_sources = route_filesystem_sources_for_query(raw_query)
            if route_sources:
                shown_sources = route_sources
                display_sources = route_sources
                reasoning_sources = route_sources
                started = time.perf_counter()
                answer = build_source_location_answer(raw_query, route_sources, query_info)
                metrics.add_stage("source_location_answer", started)
                cited_entities = extract_cited_entities(route_sources)
                response_mode = "source_location"
                memory.add(
                    raw_query, answer,
                    resolved_query=_resolved_query_text(query_info, raw_query),
                    entities=cited_entities,
                    primary_intent=primary_intent,
                )
                meta["validation"] = getattr(memory, "last_validation", None)
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
                    shown_sources=len(route_sources),
                    source_filter=meta["source_filter"],
                    response_mode="source_location",
                    evidence_confidence=evidence_confidence["level"],
                )
                _write_trace_for_query(
                    raw_query=raw_query,
                    answer=answer,
                    response_sources=route_sources,
                    expanded=expanded,
                    memory=memory,
                    metrics=metrics,
                    primary_intent=primary_intent,
                    query_info=query_info,
                )
                if return_meta:
                    return answer, route_sources, token_count, meta
                return answer, route_sources, token_count
    if has_strong_source_location_evidence(raw_query, shown_sources, query_info):
        started = time.perf_counter()
        answer = build_source_location_answer(raw_query, shown_sources, query_info)
        metrics.add_stage("source_location_answer", started)
        cited_entities = extract_cited_entities(shown_sources)
        response_mode = "source_location"
        memory.add(
            raw_query, answer,
            resolved_query=_resolved_query_text(query_info, raw_query),
            entities=cited_entities,
            primary_intent=primary_intent,
        )
        meta["validation"] = getattr(memory, "last_validation", None)
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
            response_mode = "symbol_deep_dive"
            memory.add(
                raw_query, deep_dive_answer,
                resolved_query=_resolved_query_text(query_info, raw_query),
                entities=cited_entities,
                primary_intent=primary_intent,
            )
            meta["validation"] = getattr(memory, "last_validation", None)
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
        shown_sources = rank_follow_up_sources_for_explanation(shown_sources, raw_query)
        reasoning_sources = rank_follow_up_sources_for_explanation(reasoning_sources, raw_query)
        # Weak evidence: let LLM handle instead of a thin deterministic explanation
        if evidence_confidence["level"] != "weak":
            answer = build_explanation_answer(raw_query, shown_sources, expanded)
            cited_entities = extract_cited_entities(shown_sources)
            response_mode = "explanation_summary"
            memory.add(
                raw_query, answer,
                resolved_query=_resolved_query_text(query_info, raw_query),
                entities=cited_entities,
                primary_intent=primary_intent,
            )
            meta["validation"] = getattr(memory, "last_validation", None)
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
    if is_explanation_request(raw_query):
        response_sources = rank_follow_up_sources_for_explanation(response_sources, raw_query)
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
    response_mode = "llm"
    memory.add(
        raw_query, answer,
        resolved_query=_resolved_query_text(query_info, raw_query),
        entities=cited_entities,
        primary_intent=primary_intent,
    )
    meta["validation"] = getattr(memory, "last_validation", None)
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
    latest_rendered_sources = []
    if hasattr(memory, "latest_rendered_sources"):
        try:
            latest_rendered_sources = list(memory.latest_rendered_sources() or [])
        except Exception:
            latest_rendered_sources = []
    latest_entity_set = (
        extract_cited_entities(latest_rendered_sources) if latest_rendered_sources else {}
    )
    topic_shift = detect_topic_shift(
        raw_query,
        query_info.get("entities", {}),
        recent_turns,
    )
    query_info["topic_shift"] = topic_shift

    # Calculate is_followup and is_low_context using state
    from retrieval.query_intent import identify_followup_or_low_context
    followup_entity_set = latest_entity_set or recent_entity_set
    conversation_state = {
        "previous_files": followup_entity_set.get("files", []),
        "previous_symbols": followup_entity_set.get("symbols", []),
        "previous_query": memory.latest_query()
    }
    if followup_entity_set.get("routes"):
        conversation_state["previous_routes"] = followup_entity_set.get("routes", [])
    if followup_entity_set.get("env_keys"):
        conversation_state["previous_env_keys"] = followup_entity_set.get("env_keys", [])
    is_followup_detected, is_low_context_detected = identify_followup_or_low_context(raw_query, conversation_state)

    query_info["is_followup"] = bool(is_followup_detected and not topic_shift)
    query_info["conversation_state"] = conversation_state
    from retrieval.code_answers import is_code_request
    from retrieval.query_intent import is_explanation_query, is_source_location_query
    if is_explanation_query(raw_query):
        query_info["primary_intent"] = "EXPLANATION"
        if "intent" in query_info:
            query_info["intent"] = "EXPLANATION"
    elif is_source_location_query(raw_query):
        query_info["primary_intent"] = "FILE"
        if "intent" in query_info:
            query_info["intent"] = "FILE"
    elif not is_code_request(raw_query):
        if query_info.get("primary_intent") == "CODE_REQUEST":
            query_info["primary_intent"] = "SEMANTIC"
        if query_info.get("intent") == "CODE_REQUEST":
            query_info["intent"] = "SEMANTIC"
    elif is_low_context_detected:
        query_info["primary_intent"] = "LOW_CONTEXT"

    # If topic shift detected, skip follow-up rewriting so old entities
    # don't pollute a genuinely new question.
    if topic_shift:
        return query_info

    explicit_non_impl_request = query_explicitly_requests_non_implementation_artifacts(raw_query)
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
        followup_entity_set,
        previous_resolved_query=anchor_query,
    )
    combined_info = process_query(rewritten)
    
    if is_explanation_query(raw_query):
        combined_info["primary_intent"] = "EXPLANATION"
        if "intent" in combined_info:
            combined_info["intent"] = "EXPLANATION"
    elif is_source_location_query(raw_query):
        combined_info["primary_intent"] = "FILE"
        if "intent" in combined_info:
            combined_info["intent"] = "FILE"
    elif not is_code_request(raw_query):
        orig_intent = query_info.get("primary_intent") or query_info.get("intent") or "SEMANTIC"
        if orig_intent == "CODE_REQUEST":
            orig_intent = "SEMANTIC"
        combined_info["primary_intent"] = orig_intent
        if "intent" in combined_info:
            combined_info["intent"] = orig_intent

    combined_info["is_followup"] = bool(is_followup_detected and not topic_shift and not explicit_non_impl_request)
    if is_vague_follow_up_query(raw_query) and latest_entity_set and not explicit_non_impl_request:
        combined_info["follow_up_anchor_paths"] = list(latest_entity_set.get("files", []) or [])
        combined_info["follow_up_anchor_symbols"] = list(latest_entity_set.get("symbols", []) or [])
    if is_low_context_detected and not is_explanation_query(raw_query):
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

    if query_explicitly_requests_non_implementation_artifacts(raw_query):
        return False

    entities = query_info.get("entities", {})
    if entities.get("symbols") or entities.get("files"):
        return False

    lowered = raw_query.strip().lower()
    if not lowered:
        return False

    if is_vague_follow_up_query(lowered):
        return True

    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", lowered)
    if len(tokens) <= 4:
        return True

    return any(token in FOLLOW_UP_MARKERS for token in tokens if token not in {"code", "snippet"})


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
