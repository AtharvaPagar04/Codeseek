"""Retrieval evaluation runner (hit@k + MRR@k + citation coverage)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrieval.assembler import assemble
from retrieval.expander import expand
from retrieval.memory import ConversationMemory
from retrieval.query_processor import process_query
from retrieval.searcher import search
from retrieval.source_filter import select_sources_for_display


def _load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("Invalid eval file: `cases` must be a list")
    return cases


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _matches_expectation(item: dict, expected_sources: list[dict]) -> bool:
    rp = _norm(item.get("relative_path", ""))
    sn = _norm(item.get("symbol_name", ""))
    for exp in expected_sources:
        exp_rp = _norm(exp.get("relative_path", ""))
        exp_sn = _norm(exp.get("symbol_name", ""))
        if exp_rp and rp != exp_rp:
            continue
        if exp_sn and sn != exp_sn:
            continue
        return True
    return False


def _hit_at_k(candidates: list[dict], expected_sources: list[dict], k: int) -> int:
    if not expected_sources:
        return 1
    for item in candidates[:k]:
        if _matches_expectation(item, expected_sources):
            return 1
    return 0


def _mrr_at_k(candidates: list[dict], expected_sources: list[dict], k: int) -> float:
    if not expected_sources:
        return 1.0
    for index, item in enumerate(candidates[:k], start=1):
        if _matches_expectation(item, expected_sources):
            return 1.0 / index
    return 0.0


def _citation_coverage(sources: list[dict], expected_sources: list[dict]) -> float:
    if not expected_sources:
        return 1.0
    matched = 0
    for exp in expected_sources:
        exp_rp = _norm(exp.get("relative_path", ""))
        exp_sn = _norm(exp.get("symbol_name", ""))
        found = False
        for src in sources:
            rp = _norm(src.get("relative_path", ""))
            sn = _norm(src.get("symbol_name", ""))
            if exp_rp and rp != exp_rp:
                continue
            if exp_sn and sn != exp_sn:
                continue
            found = True
            break
        if found:
            matched += 1
    return matched / max(1, len(expected_sources))


def evaluate_case(case: dict, k: int) -> dict:
    query = case["query"]
    expected_sources = case.get("expected_sources", [])

    query_info = process_query(query)
    candidates = search(query_info)
    expanded = expand(candidates, query_info)
    # no history for eval; we only need citation-style sources from assembly
    _, sources, _ = assemble(expanded, history_block=ConversationMemory(1).get_history_block())
    shown_sources = select_sources_for_display(query, sources)

    return {
        "id": case.get("id", ""),
        "query": query,
        "is_negative": not bool(expected_sources),
        "hit_at_k": _hit_at_k(candidates, expected_sources, k),
        "mrr_at_k": _mrr_at_k(candidates, expected_sources, k),
        "citation_coverage": _citation_coverage(shown_sources, expected_sources),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run retrieval eval set.")
    parser.add_argument("--eval-file", required=True, help="Path to eval JSON file")
    parser.add_argument("--k", type=int, default=10, help="K for hit@k (default: 10)")
    args = parser.parse_args()

    cases = _load_cases(Path(args.eval_file))
    if not cases:
        raise SystemExit("No eval cases found.")

    results = [evaluate_case(case, args.k) for case in cases]

    avg_hit = sum(r["hit_at_k"] for r in results) / len(results)
    avg_mrr = sum(r["mrr_at_k"] for r in results) / len(results)
    avg_cov = sum(r["citation_coverage"] for r in results) / len(results)
    pos = [r for r in results if not r["is_negative"]]
    neg = [r for r in results if r["is_negative"]]

    print("Retrieval Eval Results")
    print("======================")
    print(f"Cases: {len(results)}")
    print(f"hit@{args.k}: {avg_hit:.3f}")
    print(f"mrr@{args.k}: {avg_mrr:.3f}")
    print(f"citation_coverage: {avg_cov:.3f}")
    if pos:
        print(
            f"positive_cases: {len(pos)} | hit@{args.k}="
            f"{sum(r['hit_at_k'] for r in pos)/len(pos):.3f} | "
            f"mrr@{args.k}={sum(r['mrr_at_k'] for r in pos)/len(pos):.3f}"
        )
    if neg:
        print(
            f"negative_cases: {len(neg)} | hit@{args.k}="
            f"{sum(r['hit_at_k'] for r in neg)/len(neg):.3f} | "
            f"mrr@{args.k}={sum(r['mrr_at_k'] for r in neg)/len(neg):.3f}"
        )
    print()
    for r in results:
        print(
            f"[{r['id']}] hit@{args.k}={r['hit_at_k']} "
            f"mrr@{args.k}={r['mrr_at_k']:.2f} "
            f"citation_coverage={r['citation_coverage']:.2f} | {r['query']}"
        )


if __name__ == "__main__":
    main()
