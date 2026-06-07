"""Run CodeSeek RAGAS-style validation over a curated gold dataset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from retrieval.config import get_repo_root
from retrieval.main import run_query
from retrieval.memory import ConversationMemory
from retrieval.ragas_eval_support import (
    build_report_meta,
    build_family_baseline_snapshot,
    compare_reports,
    compare_family_baselines,
    compute_metric_bundle,
    infer_failure_stage_hint,
    load_fixture,
    render_markdown_report,
    resolve_repo_root_hint,
    serialize_context_block,
    serialize_metric_bundle,
    serialize_source_item,
    summarize_entries,
    tokenize,
    top_low_scores,
)
from retrieval.isolation import expected_collection_name


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


def _seed_memory(memory: ConversationMemory, history_turns: list[dict]) -> None:
    for turn in history_turns:
        query = str(turn.get("query", "")).strip()
        answer = str(turn.get("answer", "")).strip()
        if not query:
            continue
        memory.add(
            query,
            answer,
            resolved_query=str(turn.get("resolved_query", query)).strip(),
            entities=turn.get("entities", {}) or {},
            primary_intent=str(turn.get("primary_intent", "")).strip() or None,
        )


def _context_blocks_for_report(evaluation: dict, response_mode: str) -> list[dict]:
    if response_mode == "llm":
        blocks = evaluation.get("answer_context_blocks") or evaluation.get("reasoning_context_blocks") or []
    elif response_mode == "low_context":
        blocks = evaluation.get("answer_context_blocks") or []
    else:
        blocks = evaluation.get("answer_context_blocks") or evaluation.get("deterministic_context_blocks") or []
    return [serialize_context_block(block) for block in blocks]


def _token_count_from_blocks(blocks: list[dict]) -> int:
    return sum(len(tokenize(str(block.get("text", "")))) for block in blocks)


def _build_response_entry(
    *,
    case: dict,
    answer: str,
    returned_sources: list[dict],
    token_count: int,
    meta: dict,
    repo_root: str,
    collection_name: str,
) -> dict:
    evaluation = dict(meta.get("evaluation", {}) or {})
    query_info = evaluation.get("query_info", {}) or {}
    response_mode = str(meta.get("response_mode", "") or evaluation.get("response_mode", "")).strip()
    answer_context_blocks = _context_blocks_for_report(evaluation, response_mode)
    ground_truth = str(case.get("ground_truth", "") or "").strip()
    ground_truth_sources = case.get("ground_truth_sources", []) or []
    metric_bundle = compute_metric_bundle(
        question=str(case.get("query", "")).strip(),
        answer=answer,
        answer_context_blocks=answer_context_blocks,
        ground_truth=ground_truth,
        ground_truth_sources=ground_truth_sources,
        response_mode=response_mode,
    )

    failure_stage_hint = infer_failure_stage_hint(
        query=str(case.get("query", "")).strip(),
        response_mode=response_mode,
        expected_response_mode=str(case.get("expected_response_mode", "")).strip(),
        search_candidates=list(evaluation.get("search_candidates", []) or []),
        expanded_candidates=list(evaluation.get("expanded_candidates", []) or []),
        assembled_sources=list(evaluation.get("assembled_sources", []) or returned_sources),
        display_sources=list(evaluation.get("display_sources", []) or returned_sources),
        reasoning_sources=list(evaluation.get("reasoning_sources", []) or []),
        ground_truth_sources=ground_truth_sources,
        metric_bundle=metric_bundle,
    )

    context_token_count = _token_count_from_blocks(answer_context_blocks) or int(token_count or 0)
    reasoning_context_blocks = [
        serialize_context_block(block)
        for block in (evaluation.get("reasoning_context_blocks") or [])
    ]
    reasoning_context_token_count = int(
        evaluation.get("reasoning_context_token_count")
        or _token_count_from_blocks(reasoning_context_blocks)
        or context_token_count
    )

    return {
        "case_id": case.get("id", ""),
        "query": case.get("query", ""),
        "raw_query": case.get("query", ""),
        "resolved_query": query_info.get("raw_query", case.get("query", "")),
        "repo_root": repo_root,
        "collection_name": collection_name,
        "request_id": meta.get("request_id", ""),
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expected_response_mode": case.get("expected_response_mode", ""),
        "expected_intent": case.get("expected_intent", ""),
        "primary_intent": query_info.get("primary_intent", ""),
        "legacy_intent": query_info.get("intent", ""),
        "intent_scores": query_info.get("intent_scores", {}),
        "entities": query_info.get("entities", {}),
        "is_followup": bool(query_info.get("is_followup", False)),
        "response_mode": response_mode,
        "latency_profile": case.get("latency_profile", response_mode or "retrieval_only"),
        "stage_latency_ms": meta.get("stage_latency_ms", {}),
        "total_latency_ms": int(meta.get("total_latency_ms", 0)),
        "backend_latency_ms": int(meta.get("backend_latency_ms", meta.get("total_latency_ms", 0))),
        "provider_latency_ms": int(meta.get("provider_latency_ms", 0)),
        "evidence_confidence": meta.get("evidence_confidence", {}),
        "source_filter": meta.get("source_filter", {}),
        "search_candidates": [serialize_source_item(item) for item in evaluation.get("search_candidates", []) or []],
        "expanded_candidates": [serialize_source_item(item) for item in evaluation.get("expanded_candidates", []) or []],
        "assembled_sources": [serialize_source_item(item) for item in evaluation.get("assembled_sources", []) or []],
        "display_sources": [serialize_source_item(item) for item in evaluation.get("display_sources", []) or []],
        "reasoning_sources": [serialize_source_item(item) for item in evaluation.get("reasoning_sources", []) or []],
        "contexts": answer_context_blocks,
        "context_token_count": context_token_count,
        "reasoning_context_token_count": reasoning_context_token_count,
        "final_answer": answer,
        "ground_truth": ground_truth,
        "ground_truth_sources": [serialize_source_item(item) for item in ground_truth_sources],
        "ragas": serialize_metric_bundle(metric_bundle),
        "failure_stage_hint": failure_stage_hint,
        "context_capture_status": "complete",
        "review_notes": case.get("notes", ""),
        "manual_override_label": case.get("manual_override_label", ""),
        "expected_files": case.get("expected_files", []),
        "expected_symbols": case.get("expected_symbols", []),
        "expected_answer_terms": case.get("expected_answer_terms", []),
        "expected_context_terms": case.get("expected_context_terms", []),
    }


def _build_skipped_entry(case: dict, repo_root: str, collection_name: str, reason: str) -> dict:
    metric = {"state": "not_applicable", "detail": reason}
    return {
        "case_id": case.get("id", ""),
        "query": case.get("query", ""),
        "raw_query": case.get("query", ""),
        "resolved_query": case.get("query", ""),
        "repo_root": repo_root,
        "collection_name": collection_name,
        "request_id": "",
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expected_response_mode": case.get("expected_response_mode", ""),
        "expected_intent": case.get("expected_intent", ""),
        "primary_intent": case.get("expected_intent", ""),
        "legacy_intent": "",
        "intent_scores": {},
        "entities": {},
        "is_followup": False,
        "response_mode": "skipped",
        "latency_profile": "skipped",
        "stage_latency_ms": {},
        "total_latency_ms": 0,
        "backend_latency_ms": 0,
        "provider_latency_ms": 0,
        "evidence_confidence": {"level": "weak", "reason": reason, "count": 0},
        "source_filter": {},
        "search_candidates": [],
        "expanded_candidates": [],
        "assembled_sources": [],
        "display_sources": [],
        "reasoning_sources": [],
        "contexts": [],
        "context_token_count": 0,
        "reasoning_context_token_count": 0,
        "final_answer": "",
        "ground_truth": str(case.get("ground_truth", "") or ""),
        "ground_truth_sources": [serialize_source_item(item) for item in case.get("ground_truth_sources", []) or []],
        "ragas": {
            "context_precision": metric,
            "context_recall": metric,
            "faithfulness": metric,
            "answer_relevancy": metric,
            "answer_correctness": metric,
        },
        "failure_stage_hint": "query_understanding",
        "context_capture_status": "incomplete",
        "review_notes": case.get("notes", ""),
        "manual_override_label": case.get("manual_override_label", ""),
        "expected_files": case.get("expected_files", []),
        "expected_symbols": case.get("expected_symbols", []),
        "expected_answer_terms": case.get("expected_answer_terms", []),
        "expected_context_terms": case.get("expected_context_terms", []),
        "execution_state": "skipped",
        "skip_reason": reason,
    }


def _print_summary(report: dict) -> None:
    summary = report.get("summary", {})
    print("RAGAS Validation Results")
    print("========================")
    print(f"Cases: {report.get('run_meta', {}).get('case_count', len(report.get('responses', [])))}")
    for metric, value in summary.get("metric_averages", {}).items():
        print(f"{metric}: {float(value):.4f}")
    print()
    for metric in ("context_precision", "context_recall", "faithfulness", "answer_relevancy", "answer_correctness"):
        low = top_low_scores(report.get("responses", []), metric, limit=3)
        if not low:
            continue
        print(f"Lowest {metric}:")
        for item in low:
            print(
                f"  [{item['case_id']}] {item['value']:.4f} | {item['response_mode']} | {item['failure_stage_hint']} | {item['query']}"
            )
        print()


def _write_json(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False), encoding="utf-8")


def _write_markdown(path: Path, report: dict, previous_report: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown_report(report)
    if previous_report:
        markdown += "\n\n## Trend Comparison\n\n"
        comparison = compare_reports(report, previous_report)
        markdown += "| Metric | Delta |\n|---|---:|\n"
        for metric, delta in comparison.get("metric_deltas", {}).items():
            markdown += f"| `{metric}` | `{float(delta):+.4f}` |\n"
    path.write_text(markdown, encoding="utf-8")


def _write_family_baseline(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = build_family_baseline_snapshot(report)
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=False), encoding="utf-8")


def _append_family_baseline_comparison(markdown: str, comparison: dict) -> str:
    families = comparison.get("families", {}) if isinstance(comparison, dict) else {}
    if not families:
        return markdown

    lines = [
        "",
        "## Family Baseline Comparison",
        "",
        f"- Current snapshot: `{comparison.get('current', {}).get('dataset_name', '-')}`",
        f"- Baseline snapshot: `{comparison.get('previous', {}).get('dataset_name', '-')}`",
        "",
    ]
    for family_field in ("primary_intent", "response_mode"):
        groups = families.get(family_field, {}) or {}
        if not groups:
            continue
        lines.append(f"### `{family_field}`")
        lines.append("")
        lines.append("| Bucket | Count | Baseline Count | Δcontext_precision | Δcontext_recall | Δfaithfulness | Δanswer_relevancy | Δanswer_correctness |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for bucket, payload in sorted(groups.items()):
            deltas = payload.get("metric_deltas", {})
            lines.append(
                "| "
                f"`{bucket}` | "
                f"`{int(payload.get('current_count', 0))}` | "
                f"`{int(payload.get('previous_count', 0))}` | "
                f"`{float(deltas.get('context_precision', 0.0)):+.4f}` | "
                f"`{float(deltas.get('context_recall', 0.0)):+.4f}` | "
                f"`{float(deltas.get('faithfulness', 0.0)):+.4f}` | "
                f"`{float(deltas.get('answer_relevancy', 0.0)):+.4f}` | "
                f"`{float(deltas.get('answer_correctness', 0.0)):+.4f}` |"
            )
        lines.append("")

    return markdown + "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CodeSeek RAGAS validation.")
    parser.add_argument(
        "--eval-file",
        default="docs/retrieval_docs/eval_codeseek_ragas_v1.json",
        help="Path to the RAGAS eval fixture JSON file.",
    )
    parser.add_argument(
        "--output-json",
        default="docs/retrieval_docs/eval_results_ragas_latest.json",
        help="Path for the detailed JSON report.",
    )
    parser.add_argument(
        "--output-md",
        default="docs/retrieval_docs/eval_results_ragas_latest.md",
        help="Path for the rendered markdown report.",
    )
    parser.add_argument(
        "--compare-with",
        default="",
        help="Optional prior JSON report to include trend comparison in the markdown output.",
    )
    parser.add_argument(
        "--family-baseline",
        default="",
        help="Optional family baseline JSON snapshot to compare against in the markdown output.",
    )
    parser.add_argument(
        "--family-baseline-out",
        default="",
        help="Optional path to write the current family baseline JSON snapshot.",
    )
    parser.add_argument("--provider", default="", help="Optional provider for provider-backed eval cases")
    parser.add_argument("--api-key-env", default="", help="Env var holding the provider API key")
    parser.add_argument("--model", default="", help="Optional model override for provider-backed eval cases")
    parser.add_argument(
        "--repo-root",
        default="",
        help="Override repo root used for retrieval and report metadata.",
    )
    parser.add_argument(
        "--collection",
        default="",
        help="Override Qdrant collection used for retrieval and report metadata.",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    fixture_path = (project_root / args.eval_file).resolve()
    fixture, cases = load_fixture(fixture_path)
    if not cases:
        raise SystemExit("No eval cases found.")

    repo_root = str(Path(args.repo_root).resolve()) if args.repo_root else resolve_repo_root_hint(fixture, get_repo_root())
    collection_name = str(args.collection).strip() or expected_collection_name(repo_root)
    os.environ["RETRIEVAL_REPO_ROOT"] = repo_root
    os.environ["QDRANT_COLLECTION_NAME"] = collection_name

    provider_config = _resolve_provider_config(args)
    previous_report = None
    if args.compare_with:
        previous_report = json.loads(Path(args.compare_with).read_text(encoding="utf-8"))
    previous_family_baseline = None
    if args.family_baseline:
        previous_family_baseline = json.loads(Path(args.family_baseline).read_text(encoding="utf-8"))

    responses: list[dict] = []
    for case in cases:
        query = str(case.get("query", "")).strip()
        if not query:
            responses.append(_build_skipped_entry(case, repo_root, collection_name, "missing query"))
            continue
        memory = ConversationMemory(max_turns=1)
        _seed_memory(memory, case.get("history", []) or [])
        try:
            answer, returned_sources, token_count, meta = run_query(
                query,
                memory,
                return_meta=True,
                provider_config=provider_config,
                capture_eval=True,
            )
            entry = _build_response_entry(
                case=case,
                answer=answer,
                returned_sources=returned_sources,
                token_count=token_count,
                meta=meta,
                repo_root=repo_root,
                collection_name=collection_name,
            )
            entry["execution_state"] = "ok"
            entry["context_capture_status"] = "complete"
        except Exception as exc:  # pragma: no cover - defensive runtime reporting
            entry = _build_skipped_entry(case, repo_root, collection_name, str(exc))
            entry["execution_state"] = "error"
            entry["error_message"] = str(exc)
        responses.append(entry)

    report = {
        "run_meta": build_report_meta(
            dataset_name=str(fixture.get("name", fixture_path.stem)),
            repo_root=repo_root,
            collection_name=collection_name,
            case_count=len(responses),
            previous_report=previous_report,
        ),
        "summary": summarize_entries(responses),
        "responses": responses,
    }
    if previous_report:
        report["trend"] = compare_reports(report, previous_report)
    if previous_family_baseline:
        report["family_baseline_trend"] = compare_family_baselines(report, previous_family_baseline)

    output_json = (project_root / args.output_json).resolve()
    output_md = (project_root / args.output_md).resolve()
    _write_json(output_json, report)
    markdown = render_markdown_report(report)
    if previous_report:
        markdown += "\n\n## Trend Comparison\n\n"
        comparison = compare_reports(report, previous_report)
        markdown += "| Metric | Delta |\n|---|---:|\n"
        for metric, delta in comparison.get("metric_deltas", {}).items():
            markdown += f"| `{metric}` | `{float(delta):+.4f}` |\n"
    if previous_family_baseline:
        markdown = _append_family_baseline_comparison(markdown, report["family_baseline_trend"])
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(markdown, encoding="utf-8")
    if args.family_baseline_out:
        baseline_out = (project_root / args.family_baseline_out).resolve()
        _write_family_baseline(baseline_out, report)
    _print_summary(report)
    print(f"JSON report written to: {output_json}")
    print(f"Markdown report written to: {output_md}")
    if args.family_baseline_out:
        print(f"Family baseline written to: {baseline_out}")


if __name__ == "__main__":
    main()
