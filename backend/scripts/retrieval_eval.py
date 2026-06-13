"""Retrieval evaluation runner (hit@k + MRR@k + citation coverage)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from retrieval.assembler import assemble
from retrieval.expander import expand
from retrieval.main import run_query
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


def _expected_bool_score(actual: bool, expected: bool | None) -> float:
    if expected is None:
        return 1.0
    return 1.0 if bool(actual) is bool(expected) else 0.0


def _expected_count_score(actual: int, expected: int | bool | None) -> float:
    if expected is None:
        return 1.0
    if isinstance(expected, bool):
        return 1.0 if bool(actual > 0) is expected else 0.0
    return 1.0 if int(actual) == int(expected) else 0.0


def _forbidden_term_score(text: str, forbidden_terms: list[str]) -> float:
    if not forbidden_terms:
        return 1.0
    haystack = (text or "").lower()
    for term in forbidden_terms:
        normalized = term.strip().lower()
        if normalized and normalized in haystack:
            return 0.0
    return 1.0


def _source_term_score(items: list[dict], forbidden_terms: list[str]) -> float:
    if not forbidden_terms:
        return 1.0
    haystack = "\n".join(_item_text(item) for item in items)
    for term in forbidden_terms:
        normalized = term.strip().lower()
        if normalized and normalized in haystack:
            return 0.0
    return 1.0


def _expected_text_score(actual: str, expected: str) -> float:
    if not expected:
        return 1.0
    return 1.0 if _norm(actual) == _norm(expected) else 0.0


def _average_scores(values: list[float]) -> float:
    if not values:
        return 1.0
    return sum(values) / len(values)


def _metric_values(results: list[dict], key: str) -> list[float]:
    return [float(item[key]) for item in results if key in item]


def _actual_positive_rate(results: list[dict], key: str) -> float:
    if not results:
        return 0.0
    positives = sum(1 for item in results if bool(item.get(key)))
    return positives / len(results)


def _precision_recall(results: list[dict], *, expected_key: str, actual_key: str) -> tuple[float, float]:
    labeled = [item for item in results if item.get(expected_key) is not None]
    if not labeled:
        return 1.0, 1.0
    true_positive = sum(
        1 for item in labeled if bool(item.get(expected_key)) and bool(item.get(actual_key))
    )
    predicted_positive = sum(1 for item in labeled if bool(item.get(actual_key)))
    expected_positive = sum(1 for item in labeled if bool(item.get(expected_key)))
    precision = true_positive / predicted_positive if predicted_positive else (1.0 if expected_positive == 0 else 0.0)
    recall = true_positive / expected_positive if expected_positive else 1.0
    return precision, recall


def _extract_memory_diagnostics(meta: dict) -> tuple[dict, dict, dict]:
    diagnostics = meta.get("memory_diagnostics") if isinstance(meta.get("memory_diagnostics"), dict) else {}
    memory = diagnostics.get("memory") if isinstance(diagnostics.get("memory"), dict) else {}
    rewrite = diagnostics.get("rewrite") if isinstance(diagnostics.get("rewrite"), dict) else {}
    retrieval = diagnostics.get("retrieval") if isinstance(diagnostics.get("retrieval"), dict) else {}
    return memory, rewrite, retrieval


def _case_turns(case: dict) -> list[dict]:
    turns = case.get("turns")
    if isinstance(turns, list) and turns:
        return turns
    return []


def _turn_hit_mrr_sources(turn: dict, response_sources: list[dict]) -> tuple[int, float]:
    expected_sources = turn.get("expected_sources", [])
    expected_files = turn.get("expected_files", [])
    expected_symbols = turn.get("expected_symbols", [])
    hit = _hit_at_k(response_sources, expected_sources, expected_files, expected_symbols, len(response_sources) or 1)
    mrr = _mrr_at_k(response_sources, expected_sources, expected_files, expected_symbols, len(response_sources) or 1)
    return hit, mrr


def _evaluate_turn_sequence_case(case: dict, provider_config: dict | None = None) -> dict:
    memory = ConversationMemory(max(4, len(_case_turns(case)) + 1))
    turn_results: list[dict] = []

    for index, turn in enumerate(_case_turns(case), start=1):
        query = str(turn["query"])
        answer, response_sources, _, meta = run_query(
            query,
            memory,
            return_meta=True,
            provider_config=provider_config,
        )
        memory_diag, rewrite_diag, retrieval_diag = _extract_memory_diagnostics(meta)
        hit_at_k, mrr_at_k = _turn_hit_mrr_sources(turn, response_sources)
        previous_candidates_injected = int(retrieval_diag.get("previous_candidates_injected", 0) or 0)
        low_confidence_gate = bool(retrieval_diag.get("low_confidence_gate", False))
        actual_response_mode = str(meta.get("response_mode", ""))
        expected_retrieval_confidence = str(turn.get("expected_retrieval_confidence", "") or "").strip()
        result = {
            "id": f"{case.get('id', 'case')}.t{index}",
            "query": query,
            "hit_at_k": hit_at_k,
            "mrr_at_k": mrr_at_k,
            "citation_coverage": _citation_coverage(response_sources, turn.get("expected_sources", [])),
            "expected_file_score": _expected_file_score(response_sources, turn.get("expected_files", [])),
            "expected_symbol_score": _expected_symbol_score(response_sources, turn.get("expected_symbols", [])),
            "expected_framework_score": _expected_term_score(response_sources, turn.get("expected_frameworks", [])),
            "expected_dependency_score": _expected_term_score(response_sources, turn.get("expected_dependencies", [])),
            "expected_no_answer_score": _expected_no_answer_score(
                response_sources, response_sources, bool(turn.get("expected_no_answer", False))
            ),
            "expected_response_mode_score": _expected_response_mode_score(
                actual_response_mode,
                str(turn.get("expected_response_mode", "")).strip(),
            ),
            "expected_answer_term_score": _expected_answer_term_score(
                answer,
                turn.get("expected_answer_terms", []),
            ),
            "followup_decision_score": _expected_bool_score(
                bool(memory_diag.get("is_followup", False)),
                turn.get("expected_is_followup"),
            ),
            "topic_shift_score": _expected_bool_score(
                bool(memory_diag.get("topic_shift_detected", False)),
                turn.get("expected_topic_shift"),
            ),
            "history_injection_score": _expected_bool_score(
                bool(memory_diag.get("history_injected", False)),
                turn.get("expected_history_injected"),
            ),
            "previous_candidate_injection_score": _expected_count_score(
                previous_candidates_injected,
                turn.get("expected_previous_candidates_injected"),
            ),
            "query_rewrite_score": _expected_bool_score(
                bool(rewrite_diag.get("query_rewritten", False)),
                turn.get("expected_query_rewritten"),
            ),
            "low_confidence_refusal_score": _expected_bool_score(
                low_confidence_gate or actual_response_mode == "low_context",
                turn.get("expected_low_confidence_gate"),
            ),
            "wrong_topic_answer_score": _forbidden_term_score(
                answer,
                turn.get("forbidden_answer_terms", []),
            ),
            "wrong_topic_source_score": _source_term_score(
                response_sources,
                turn.get("forbidden_source_terms", []),
            ),
            "retrieval_confidence_score": _expected_text_score(
                str(retrieval_diag.get("retrieval_confidence", "") or ""),
                expected_retrieval_confidence,
            ),
            "response_mode": actual_response_mode,
            "latency_profile": _latency_profile_for_case(turn, actual_response_mode),
            "total_latency_ms": int(meta.get("total_latency_ms", 0) or 0),
            "backend_latency_ms": int(meta.get("backend_latency_ms", 0) or 0),
            "provider_latency_ms": int(meta.get("provider_latency_ms", 0) or 0),
            "stage_latency_ms": dict(meta.get("stage_latency_ms", {})),
            "expected_is_followup": turn.get("expected_is_followup"),
            "actual_is_followup": bool(memory_diag.get("is_followup", False)),
            "expected_history_injected": turn.get("expected_history_injected"),
            "actual_history_injected": bool(memory_diag.get("history_injected", False)),
            "expected_query_rewritten": turn.get("expected_query_rewritten"),
            "actual_query_rewritten": bool(rewrite_diag.get("query_rewritten", False)),
            "expected_low_confidence_gate": turn.get("expected_low_confidence_gate"),
            "actual_low_confidence_gate": low_confidence_gate or actual_response_mode == "low_context",
            "actual_previous_candidates_injected": previous_candidates_injected,
        }
        turn_results.append(result)

    followup_precision, followup_recall = _precision_recall(
        turn_results,
        expected_key="expected_is_followup",
        actual_key="actual_is_followup",
    )
    return {
        "id": case.get("id", ""),
        "query": " | ".join(turn["query"] for turn in _case_turns(case)),
        "is_negative": any(bool(turn.get("expected_no_answer", False)) for turn in _case_turns(case)),
        "hit_at_k": _average_scores(_metric_values(turn_results, "hit_at_k")),
        "mrr_at_k": _average_scores(_metric_values(turn_results, "mrr_at_k")),
        "citation_coverage": _average_scores(_metric_values(turn_results, "citation_coverage")),
        "expected_file_score": _average_scores(_metric_values(turn_results, "expected_file_score")),
        "expected_symbol_score": _average_scores(_metric_values(turn_results, "expected_symbol_score")),
        "expected_framework_score": _average_scores(_metric_values(turn_results, "expected_framework_score")),
        "expected_dependency_score": _average_scores(_metric_values(turn_results, "expected_dependency_score")),
        "expected_no_answer_score": _average_scores(_metric_values(turn_results, "expected_no_answer_score")),
        "expected_response_mode_score": _average_scores(_metric_values(turn_results, "expected_response_mode_score")),
        "expected_answer_term_score": _average_scores(_metric_values(turn_results, "expected_answer_term_score")),
        "followup_decision_score": _average_scores(_metric_values(turn_results, "followup_decision_score")),
        "topic_shift_score": _average_scores(_metric_values(turn_results, "topic_shift_score")),
        "history_injection_score": _average_scores(_metric_values(turn_results, "history_injection_score")),
        "previous_candidate_injection_score": _average_scores(_metric_values(turn_results, "previous_candidate_injection_score")),
        "query_rewrite_score": _average_scores(_metric_values(turn_results, "query_rewrite_score")),
        "low_confidence_refusal_score": _average_scores(_metric_values(turn_results, "low_confidence_refusal_score")),
        "wrong_topic_answer_score": _average_scores(_metric_values(turn_results, "wrong_topic_answer_score")),
        "wrong_topic_source_score": _average_scores(_metric_values(turn_results, "wrong_topic_source_score")),
        "retrieval_confidence_score": _average_scores(_metric_values(turn_results, "retrieval_confidence_score")),
        "followup_precision": followup_precision,
        "followup_recall": followup_recall,
        "history_injection_rate": _actual_positive_rate(turn_results, "actual_history_injected"),
        "previous_candidate_injection_rate": _actual_positive_rate(
            [{"actual_previous_candidates_injected": item["actual_previous_candidates_injected"] > 0} for item in turn_results],
            "actual_previous_candidates_injected",
        ),
        "query_rewrite_rate": _actual_positive_rate(turn_results, "actual_query_rewritten"),
        "low_confidence_refusal_rate": _actual_positive_rate(turn_results, "actual_low_confidence_gate"),
        "response_mode": "",
        "latency_profile": "llm" if any(item["latency_profile"] == "llm" for item in turn_results) else "deterministic",
        "total_latency_ms": sum(int(item["total_latency_ms"]) for item in turn_results),
        "backend_latency_ms": sum(int(item["backend_latency_ms"]) for item in turn_results),
        "provider_latency_ms": sum(int(item["provider_latency_ms"]) for item in turn_results),
        "stage_latency_ms": {},
        "turn_results": turn_results,
    }


def _latency_profile_for_case(case: dict, response_mode: str) -> str:
    explicit = str(case.get("latency_profile", "")).strip().lower()
    if explicit in {"retrieval_only", "deterministic", "llm"}:
        return explicit
    if not response_mode:
        return "retrieval_only"
    if response_mode == "llm":
        return "llm"
    return "deterministic"


def _resolve_provider_config(args: argparse.Namespace) -> dict | None:
    provider = str(getattr(args, "provider", "") or "").strip().lower()
    api_key_env = str(getattr(args, "api_key_env", "") or "").strip()
    model = str(getattr(args, "model", "") or "").strip()
    if not provider or not api_key_env:
        return None
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"Provider API key env var is empty: {api_key_env}")
    payload = {"provider": provider, "api_key": api_key}
    if model:
        payload["model"] = model
    return payload


def evaluate_case(case: dict, k: int, provider_config: dict | None = None) -> dict:
    if _case_turns(case):
        return _evaluate_turn_sequence_case(case, provider_config=provider_config)

    query = case["query"]
    expected_sources = case.get("expected_sources", [])
    expected_files = case.get("expected_files", [])
    expected_symbols = case.get("expected_symbols", [])
    expected_frameworks = case.get("expected_frameworks", [])
    expected_dependencies = case.get("expected_dependencies", [])
    expected_no_answer = bool(case.get("expected_no_answer", False))
    expected_response_mode = str(case.get("expected_response_mode", "")).strip()
    expected_answer_terms = case.get("expected_answer_terms", [])

    import time

    t0 = time.perf_counter()
    query_info = process_query(query)
    t1 = time.perf_counter()
    candidates = search(query_info)
    t2 = time.perf_counter()
    expanded = expand(candidates, query_info)
    t3 = time.perf_counter()
    # no history for eval; we only need citation-style sources from assembly
    _, sources, _ = assemble(expanded, history_block=ConversationMemory(1).get_history_block())
    t4 = time.perf_counter()
    shown_sources = select_sources_for_display(query, sources)
    t5 = time.perf_counter()

    response_mode = ""
    total_latency_ms = int((t5 - t0) * 1000)
    backend_latency_ms = total_latency_ms
    provider_latency_ms = 0
    stage_latency_ms = {
        "query_processor": int((t1 - t0) * 1000),
        "search": int((t2 - t1) * 1000),
        "expand": int((t3 - t2) * 1000),
        "assemble": int((t4 - t3) * 1000),
        "select_sources": int((t5 - t4) * 1000),
    }
    expected_response_mode_score = 1.0
    expected_answer_term_score = 1.0

    if expected_response_mode or expected_answer_terms:
        answer, response_sources, _, meta = run_query(
            query,
            ConversationMemory(1),
            return_meta=True,
            provider_config=provider_config,
        )
        shown_sources = response_sources
        response_mode = str(meta.get("response_mode", ""))
        total_latency_ms = int(meta.get("total_latency_ms", 0))
        backend_latency_ms = int(meta.get("backend_latency_ms", total_latency_ms))
        provider_latency_ms = int(meta.get("provider_latency_ms", 0))
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
        "followup_decision_score": 1.0,
        "topic_shift_score": 1.0,
        "history_injection_score": 1.0,
        "previous_candidate_injection_score": 1.0,
        "query_rewrite_score": 1.0,
        "low_confidence_refusal_score": 1.0,
        "wrong_topic_answer_score": 1.0,
        "wrong_topic_source_score": 1.0,
        "retrieval_confidence_score": 1.0,
        "followup_precision": 1.0,
        "followup_recall": 1.0,
        "history_injection_rate": 0.0,
        "previous_candidate_injection_rate": 0.0,
        "query_rewrite_rate": 0.0,
        "low_confidence_refusal_rate": 0.0,
        "response_mode": response_mode,
        "latency_profile": _latency_profile_for_case(case, response_mode),
        "total_latency_ms": total_latency_ms,
        "backend_latency_ms": backend_latency_ms,
        "provider_latency_ms": provider_latency_ms,
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
    parser.add_argument("--provider", default="", help="Optional provider for provider-backed LLM eval cases")
    parser.add_argument("--api-key-env", default="", help="Env var holding the provider API key")
    parser.add_argument("--model", default="", help="Optional model override for provider-backed LLM eval cases")
    args = parser.parse_args()

    cases = _load_cases(Path(args.eval_file))
    if not cases:
        raise SystemExit("No eval cases found.")

    provider_config = _resolve_provider_config(args)
    results = [evaluate_case(case, args.k, provider_config=provider_config) for case in cases]

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
    avg_followup = sum(r["followup_decision_score"] for r in results) / len(results)
    avg_topic_shift = sum(r["topic_shift_score"] for r in results) / len(results)
    avg_history_injection = sum(r["history_injection_score"] for r in results) / len(results)
    avg_previous_candidate_injection = sum(r["previous_candidate_injection_score"] for r in results) / len(results)
    avg_query_rewrite = sum(r["query_rewrite_score"] for r in results) / len(results)
    avg_low_confidence = sum(r["low_confidence_refusal_score"] for r in results) / len(results)
    avg_wrong_topic_answer = sum(r["wrong_topic_answer_score"] for r in results) / len(results)
    avg_wrong_topic_source = sum(r["wrong_topic_source_score"] for r in results) / len(results)
    avg_retrieval_confidence = sum(r["retrieval_confidence_score"] for r in results) / len(results)
    avg_followup_precision = sum(r["followup_precision"] for r in results) / len(results)
    avg_followup_recall = sum(r["followup_recall"] for r in results) / len(results)
    avg_history_injection_rate = sum(r["history_injection_rate"] for r in results) / len(results)
    avg_previous_candidate_injection_rate = sum(r["previous_candidate_injection_rate"] for r in results) / len(results)
    avg_query_rewrite_rate = sum(r["query_rewrite_rate"] for r in results) / len(results)
    avg_low_confidence_rate = sum(r["low_confidence_refusal_rate"] for r in results) / len(results)
    latency_values = [int(r["total_latency_ms"]) for r in results if int(r["total_latency_ms"]) > 0]
    retrieval_only_values = [
        int(r["total_latency_ms"])
        for r in results
        if r["latency_profile"] == "retrieval_only" and int(r["total_latency_ms"]) > 0
    ]
    deterministic_values = [
        int(r["total_latency_ms"])
        for r in results
        if r["latency_profile"] == "deterministic" and int(r["total_latency_ms"]) > 0
    ]
    llm_total_values = [
        int(r["total_latency_ms"])
        for r in results
        if r["latency_profile"] == "llm" and int(r["total_latency_ms"]) > 0
    ]
    llm_backend_values = [
        int(r["backend_latency_ms"])
        for r in results
        if r["latency_profile"] == "llm" and int(r["backend_latency_ms"]) > 0
    ]
    llm_provider_values = [
        int(r["provider_latency_ms"])
        for r in results
        if r["latency_profile"] == "llm" and int(r["provider_latency_ms"]) >= 0
    ]
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
    print(f"topic_shift_accuracy: {avg_topic_shift:.3f}")
    print(f"followup_precision: {avg_followup_precision:.3f}")
    print(f"followup_recall: {avg_followup_recall:.3f}")
    print(f"followup_decision_score: {avg_followup:.3f}")
    print(f"history_injection_score: {avg_history_injection:.3f}")
    print(f"previous_candidate_injection_score: {avg_previous_candidate_injection:.3f}")
    print(f"query_rewrite_score: {avg_query_rewrite:.3f}")
    print(f"low_confidence_refusal_score: {avg_low_confidence:.3f}")
    print(f"answer_relevance_score: {avg_answer_terms:.3f}")
    print(f"source_faithfulness_score: {avg_wrong_topic_source:.3f}")
    print(f"wrong_topic_answer_score: {avg_wrong_topic_answer:.3f}")
    print(f"retrieval_confidence_score: {avg_retrieval_confidence:.3f}")
    print(f"history_injection_rate: {avg_history_injection_rate:.3f}")
    print(f"previous_candidate_injection_rate: {avg_previous_candidate_injection_rate:.3f}")
    print(f"query_rewrite_rate: {avg_query_rewrite_rate:.3f}")
    print(f"low_confidence_refusal_rate: {avg_low_confidence_rate:.3f}")
    print(f"latency_p50_ms: {_p50(latency_values)}")
    print(f"latency_p95_ms: {_p95(latency_values)}")
    print(f"retrieval_only_latency_p50_ms: {_p50(retrieval_only_values)}")
    print(f"retrieval_only_latency_p95_ms: {_p95(retrieval_only_values)}")
    print(f"deterministic_latency_p50_ms: {_p50(deterministic_values)}")
    print(f"deterministic_latency_p95_ms: {_p95(deterministic_values)}")
    print(f"llm_backend_latency_p50_ms: {_p50(llm_backend_values)}")
    print(f"llm_backend_latency_p95_ms: {_p95(llm_backend_values)}")
    print(f"llm_provider_latency_p50_ms: {_p50(llm_provider_values)}")
    print(f"llm_provider_latency_p95_ms: {_p95(llm_provider_values)}")
    print(f"llm_total_latency_p50_ms: {_p50(llm_total_values)}")
    print(f"llm_total_latency_p95_ms: {_p95(llm_total_values)}")
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
            f"latency_profile={r['latency_profile']} "
            f"backend_latency_ms={r['backend_latency_ms']} "
            f"provider_latency_ms={r['provider_latency_ms']} "
            f"latency_ms={r['total_latency_ms']} | {r['query']}"
        )


if __name__ == "__main__":
    main()
