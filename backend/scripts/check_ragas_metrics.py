"""Validate CodeSeek RAGAS report thresholds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _metric_average(report: dict, metric: str) -> float:
    return float(report.get("summary", {}).get("metric_averages", {}).get(metric, 0.0) or 0.0)


def _count_low_scores(report: dict, metric: str, threshold: float) -> int:
    count = 0
    for response in report.get("responses", []):
        cell = response.get("ragas", {}).get(metric, {})
        if cell.get("state") != "numeric":
            continue
        if float(cell.get("value", 0.0) or 0.0) < threshold:
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate RAGAS report thresholds.")
    parser.add_argument("--report", required=True, help="Path to the JSON report written by ragas_eval.py")
    parser.add_argument("--min-context-precision", type=float, default=0.85)
    parser.add_argument("--min-context-recall", type=float, default=0.85)
    parser.add_argument("--min-faithfulness", type=float, default=0.90)
    parser.add_argument("--min-answer-relevancy", type=float, default=0.85)
    parser.add_argument("--min-answer-correctness", type=float, default=0.80)
    parser.add_argument("--warn-threshold", type=float, default=0.70)
    parser.add_argument("--max-low-score-count", type=int, default=0)
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    checks = [
        ("context_precision", args.min_context_precision),
        ("context_recall", args.min_context_recall),
        ("faithfulness", args.min_faithfulness),
        ("answer_relevancy", args.min_answer_relevancy),
        ("answer_correctness", args.min_answer_correctness),
    ]

    failures: list[str] = []
    print("Parsed metrics:")
    for metric, threshold in checks:
        average = _metric_average(report, metric)
        print(f"  {metric}={average:.4f} (min {threshold:.4f})")
        if average < threshold:
            failures.append(f"{metric} {average:.4f} < {threshold:.4f}")

    low_score_total = 0
    for metric in ("context_precision", "context_recall", "faithfulness", "answer_relevancy", "answer_correctness"):
        low_score_total += _count_low_scores(report, metric, args.warn_threshold)
    print(f"Low-score responses below {args.warn_threshold:.2f}: {low_score_total}")
    if low_score_total > args.max_low_score_count:
        failures.append(
            f"low-score response count {low_score_total} > allowed {args.max_low_score_count}"
        )

    if failures:
        print("Threshold check failed:")
        for failure in failures:
            print(f" - {failure}")
        sys.exit(1)

    print("Threshold check passed.")


if __name__ == "__main__":
    main()
