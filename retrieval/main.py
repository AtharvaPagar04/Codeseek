"""Entry point for retrieval pipeline."""

import argparse
import os
import time

from retrieval.assembler import assemble
from retrieval.config import (
    CONVERSATION_HISTORY_TURNS,
    MAX_CONTEXT_TOKENS,
    get_collection_name,
    get_repo_root,
)
from retrieval.expander import expand
from retrieval.llm import generate_answer
from retrieval.memory import ConversationMemory
from retrieval.observability import StageMetrics, log_event, new_request_id
from retrieval.query_processor import process_query
from retrieval.isolation import validate_collection_binding
from retrieval.searcher import search
from retrieval.source_filter import (
    explain_source_filter_decision,
    select_sources_for_display,
)


def run_query(
    raw_query: str,
    memory: ConversationMemory,
    request_id: str | None = None,
    return_meta: bool = False,
) -> tuple[str, list[dict], int] | tuple[str, list[dict], int, dict]:
    """Run one retrieval query end-to-end."""
    rid = request_id or new_request_id()
    metrics = StageMetrics(request_id=rid)
    meta: dict = {"request_id": rid}
    log_event("retrieval.request.start", rid, query=raw_query)
    validate_collection_binding(get_collection_name(), get_repo_root())
    started = time.perf_counter()
    history_block = memory.get_history_block()
    metrics.add_stage("history", started)
    started = time.perf_counter()
    query_info = process_query(raw_query)
    metrics.add_stage("query_processor", started)
    started = time.perf_counter()
    candidates = search(query_info)
    metrics.add_stage("search", started)
    started = time.perf_counter()
    expanded = expand(candidates, query_info)
    metrics.add_stage("expand", started)
    started = time.perf_counter()
    context, sources, token_count = assemble(expanded, history_block)
    metrics.add_stage("assemble", started)
    meta["source_filter"] = explain_source_filter_decision(raw_query, sources)
    shown_sources = select_sources_for_display(raw_query, sources)
    if not shown_sources:
        answer = "Not found in retrieved context."
        memory.add(raw_query, answer)
        meta.update(
            {
                "stage_latency_ms": metrics.stage_latency_ms,
                "total_latency_ms": metrics.total_ms(),
                "errors": metrics.errors,
            }
        )
        log_event(
            "retrieval.request.end",
            rid,
            status="ok",
            fallback="no_sources",
            stage_latency_ms=metrics.stage_latency_ms,
            total_latency_ms=metrics.total_ms(),
            source_filter=meta["source_filter"],
        )
        if return_meta:
            return answer, shown_sources, token_count, meta
        return answer, shown_sources, token_count
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
        context, _, token_count = assemble(llm_chunks, history_block)
    started = time.perf_counter()
    answer = generate_answer(
        raw_query,
        context,
        history_block,
        allowed_sources=shown_sources,
    )
    metrics.add_stage("llm", started)
    memory.add(raw_query, answer)
    meta.update(
        {
            "stage_latency_ms": metrics.stage_latency_ms,
            "total_latency_ms": metrics.total_ms(),
            "errors": metrics.errors,
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
    )
    if return_meta:
        return answer, shown_sources, token_count, meta
    return answer, shown_sources, token_count


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
