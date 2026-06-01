"""Entry point for retrieval pipeline."""

import argparse

from retrieval.assembler import assemble
from retrieval.config import CONVERSATION_HISTORY_TURNS, MAX_CONTEXT_TOKENS, REPO_ROOT
from retrieval.expander import expand
from retrieval.llm import generate_answer
from retrieval.memory import ConversationMemory
from retrieval.query_processor import process_query
from retrieval.searcher import search


def run_query(raw_query: str, memory: ConversationMemory) -> tuple[str, list[dict], int]:
    """Run one retrieval query end-to-end."""
    history_block = memory.get_history_block()
    query_info = process_query(raw_query)
    candidates = search(query_info)
    expanded = expand(candidates, query_info)
    context, sources, token_count = assemble(expanded, history_block)
    answer = generate_answer(raw_query, context, history_block)
    memory.add(raw_query, answer)
    return answer, sources, token_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local retrieval pipeline")
    parser.add_argument("--query", help="Single query mode", default="")
    args = parser.parse_args()

    memory = ConversationMemory(max_turns=CONVERSATION_HISTORY_TURNS)

    if args.query:
        answer, sources, token_count = run_query(args.query, memory)
        _print_result(answer, sources, token_count)
        return

    print("Codeseek retrieval ready. Type your question or 'exit'.")
    print(f"Repository root: {REPO_ROOT}")
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
        _print_result(answer, sources, token_count)


def _print_result(answer: str, sources: list[dict], token_count: int) -> None:
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
