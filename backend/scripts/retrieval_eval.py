"""Retrieval evaluation runner (hit@k + MRR@k + citation coverage)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrieval.assembler import assemble
from retrieval.expander import expand
from retrieval.memory import ConversationMemory
from retrieval.query_processor import process_query
from retrieval.main import run_query
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


def _matches_file_or_symbol(item: dict, expected_files: list[str], expected_symbols: list[str]) -> bool:
    rp = _norm(item.get("relative_path", ""))
    sn = _norm(item.get("symbol_name", ""))
    for file_path in expected_files:
        if rp == _norm(file_path):
            return True
    for symbol in expected_symbols:
        if sn == _norm(symbol):
            return True
    return False


def _item_text(item: dict) -> str:
    parts = [
        item.get("relative_path", ""),
        item.get("symbol_name", ""),
        item.get("qualified_symbol", ""),
        item.get("summary", ""),
        item.get("signature", ""),
        item.get("docstring", ""),
    ]
    for key in (
        "imports",
        "calls",
        "parameters",
        "methods",
        "file_symbols",
        "summary_facts",
        "detected_frameworks",
        "dependencies",
        "dev_dependencies",
        "services",
        "ports",
        "env_keys",
        "entrypoints",
        "config_tools",
        "setup_steps",
        "usage_commands",
        "architecture_notes",
        "content_excerpt",
    ):
        value = item.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
        elif isinstance(value, dict):
            parts.extend(str(k) for k in value)
            parts.extend(str(v) for v in value.values())
        elif value:
            parts.append(str(value))
    return " ".join(str(part) for part in parts if part).lower()


def _hit_at_k(
    candidates: list[dict],
    expected_sources: list[dict],
    expected_files: list[str],
    expected_symbols: list[str],
    k: int,
) -> int:
    if not expected_sources and not expected_files and not expected_symbols:
        return 1
    for item in candidates[:k]:
        if _matches_expectation(item, expected_sources) or _matches_file_or_symbol(
            item, expected_files, expected_symbols
        ):
            return 1
    return 0


def _mrr_at_k(
    candidates: list[dict],
    expected_sources: list[dict],
    expected_files: list[str],
    expected_symbols: list[str],
    k: int,
) -> float:
    if not expected_sources and not expected_files and not expected_symbols:
        return 1.0
    for index, item in enumerate(candidates[:k], start=1):
        if _matches_expectation(item, expected_sources) or _matches_file_or_symbol(
            item, expected_files, expected_symbols
        ):
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


def _expected_file_score(items: list[dict], expected_files: list[str]) -> float:
    if not expected_files:
        return 1.0
    found = {str(item.get("relative_path", "")).strip().lower() for item in items}
    matched = sum(1 for file_path in expected_files if file_path.strip().lower() in found)
    return matched / len(expected_files)


def _expected_symbol_score(items: list[dict], expected_symbols: list[str]) -> float:
    if not expected_symbols:
        return 1.0
    found = {
        str(item.get("symbol_name", "")).strip().lower()
        for item in items
        if item.get("symbol_name")
    }
    matched = sum(1 for symbol in expected_symbols if symbol.strip().lower() in found)
    return matched / len(expected_symbols)


def _expected_term_score(items: list[dict], expected_terms: list[str]) -> float:
    if not expected_terms:
        return 1.0
    haystack = "\n".join(_item_text(item) for item in items)
    matched = sum(1 for term in expected_terms if term.strip().lower() in haystack)
    return matched / len(expected_terms)


def _expected_no_answer_score(candidates: list[dict], shown_sources: list[dict], expected_no_answer: bool) -> float:
    if not expected_no_answer:
        return 1.0
    return 1.0 if not candidates and not shown_sources else 0.0


def _expected_response_mode_score(actual: str, expected: str) -> float:
    if not expected:
        return 1.0
    return 1.0 if _norm(actual) == _norm(expected) else 0.0


def _expected_answer_term_score(answer: str, expected_terms: list[str]) -> float:
    if not expected_terms:
        return 1.0
    haystack = answer.lower()
    matched = sum(1 for term in expected_terms if term.strip().lower() in haystack)
    return matched / len(expected_terms)


def evaluate_case(case: dict, k: int) -> dict:
    query = case["query"]
    expected_sources = case.get("expected_sources", [])
    expected_files = case.get("expected_files", [])
    expected_symbols = case.get("expected_symbols", [])
    expected_frameworks = case.get("expected_frameworks", [])
    expected_dependencies = case.get("expected_dependencies", [])
    expected_no_answer = bool(case.get("expected_no_answer", False))
    expected_response_mode = str(case.get("expected_response_mode", "")).strip()
    expected_answer_terms = case.get("expected_answer_terms", [])

    query_info = process_query(query)
    candidates = search(query_info)
    expanded = expand(candidates, query_info)
    # no history for eval; we only need citation-style sources from assembly
    _, sources, _ = assemble(expanded, history_block=ConversationMemory(1).get_history_block())
    shown_sources = select_sources_for_display(query, sources)
    response_mode = ""
    total_latency_ms = 0
    stage_latency_ms: dict = {}
    expected_response_mode_score = 1.0
    expected_answer_term_score = 1.0
    if expected_response_mode or expected_answer_terms:
        answer, response_sources, _, meta = run_query(query, ConversationMemory(1), return_meta=True)
        shown_sources = response_sources
        response_mode = str(meta.get("response_mode", ""))
        total_latency_ms = int(meta.get("total_latency_ms", 0))
        stage_latency_ms = dict(meta.get("stage_latency_ms", {}))
        expected_response_mode_score = _expected_response_mode_score(
            response_mode, expected_response_mode
        )
        expected_answer_term_score = _expected_answer_term_score(
            answer, expected_answer_terms
        )

    return {
        "id": case.get("id", ""),
        "query": query,
        "is_negative": expected_no_answer,
        "hit_at_k": _hit_at_k(candidates, expected_sources, expected_files, expected_symbols, k),
        "mrr_at_k": _mrr_at_k(candidates, expected_sources, expected_files, expected_symbols, k),
        "citation_coverage": _citation_coverage(shown_sources, expected_sources),
        "expected_file_score": _expected_file_score(candidates, expected_files),
        "expected_symbol_score": _expected_symbol_score(candidates, expected_symbols),
        "expected_framework_score": _expected_term_score(candidates, expected_frameworks),
        "expected_dependency_score": _expected_term_score(candidates, expected_dependencies),
        "expected_no_answer_score": _expected_no_answer_score(candidates, shown_sources, expected_no_answer),
        "expected_response_mode_score": expected_response_mode_score,
        "expected_answer_term_score": expected_answer_term_score,
        "response_mode": response_mode,
        "total_latency_ms": total_latency_ms,
        "stage_latency_ms": stage_latency_ms,
    }


def _p50(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return ordered[index]


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
    avg_file = sum(r["expected_file_score"] for r in results) / len(results)
    avg_symbol = sum(r["expected_symbol_score"] for r in results) / len(results)
    avg_framework = sum(r["expected_framework_score"] for r in results) / len(results)
    avg_dependency = sum(r["expected_dependency_score"] for r in results) / len(results)
    avg_no_answer = sum(r["expected_no_answer_score"] for r in results) / len(results)
    avg_response_mode = sum(r["expected_response_mode_score"] for r in results) / len(results)
    avg_answer_terms = sum(r["expected_answer_term_score"] for r in results) / len(results)
    latency_values = [int(r["total_latency_ms"]) for r in results if int(r["total_latency_ms"]) > 0]
    pos = [r for r in results if not r["is_negative"]]
    neg = [r for r in results if r["is_negative"]]

    print("Retrieval Eval Results")
    print("======================")
    print(f"Cases: {len(results)}")
    print(f"hit@{args.k}: {avg_hit:.3f}")
    print(f"mrr@{args.k}: {avg_mrr:.3f}")
    print(f"citation_coverage: {avg_cov:.3f}")
    print(f"expected_file_score: {avg_file:.3f}")
    print(f"expected_symbol_score: {avg_symbol:.3f}")
    print(f"expected_framework_score: {avg_framework:.3f}")
    print(f"expected_dependency_score: {avg_dependency:.3f}")
    print(f"expected_no_answer_score: {avg_no_answer:.3f}")
    print(f"expected_response_mode_score: {avg_response_mode:.3f}")
    print(f"expected_answer_term_score: {avg_answer_terms:.3f}")
    print(f"latency_p50_ms: {_p50(latency_values)}")
    print(f"latency_p95_ms: {_p95(latency_values)}")
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
            f"citation_coverage={r['citation_coverage']:.2f} "
            f"expected_file={r['expected_file_score']:.2f} "
            f"expected_symbol={r['expected_symbol_score']:.2f} "
            f"expected_framework={r['expected_framework_score']:.2f} "
            f"expected_dependency={r['expected_dependency_score']:.2f} "
            f"expected_no_answer={r['expected_no_answer_score']:.2f} "
            f"expected_response_mode={r['expected_response_mode_score']:.2f} "
            f"expected_answer_terms={r['expected_answer_term_score']:.2f} "
            f"response_mode={r['response_mode'] or '-'} "
            f"latency_ms={r['total_latency_ms']} | {r['query']}"
        )


if __name__ == "__main__":
    main()
