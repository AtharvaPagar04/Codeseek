"""Check a RAGAS report against a curated human-reviewed benchmark set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric_value(response: dict, metric: str) -> tuple[str, float]:
    cell = response.get("ragas", {}).get(metric, {})
    state = str(cell.get("state", ""))
    value = float(cell.get("value", 0.0) or 0.0) if state == "numeric" else 0.0
    return state, value


def _matches_expected_mode(response: dict, expected_mode: str) -> bool:
    if not expected_mode:
        return True
    actual_mode = str(response.get("response_mode", "") or "").strip()
    return actual_mode == expected_mode


def _check_case(response: dict, benchmark_case: dict) -> list[str]:
    failures: list[str] = []
    expected_mode = str(benchmark_case.get("expected_response_mode", "") or "").strip()
    if not _matches_expected_mode(response, expected_mode):
        failures.append(
            f"response_mode {response.get('response_mode', '-')!r} != expected {expected_mode!r}"
        )

    if str(benchmark_case.get("review_status", "")).strip() != "approved":
        return failures

    for metric, threshold in (benchmark_case.get("minimums", {}) or {}).items():
        state, value = _metric_value(response, metric)
        if state != "numeric":
            failures.append(f"{metric} is {state}, expected numeric >= {threshold}")
            continue
        if value < float(threshold):
            failures.append(f"{metric} {value:.4f} < {float(threshold):.4f}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a RAGAS report against a human-reviewed benchmark.")
    parser.add_argument("--report", required=True, help="Path to the JSON report written by ragas_eval.py")
    parser.add_argument(
        "--benchmark",
        default="docs/retrieval_docs/ragas_human_review_benchmark_v1.json",
        help="Path to the human-reviewed benchmark JSON file.",
    )
    args = parser.parse_args()

    report = _load_json(Path(args.report))
    benchmark = _load_json(Path(args.benchmark))
    responses = {str(item.get("case_id", "")): item for item in report.get("responses", [])}
    cases = benchmark.get("cases", [])

    approved_total = 0
    followup_total = 0
    failures: list[str] = []

    print("Human-reviewed benchmark check")
    print("==============================")
    print(f"Benchmark: {benchmark.get('name', '-')}")
    print(f"Report: {report.get('run_meta', {}).get('dataset_name', '-')}")
    print()

    for case in cases:
        case_id = str(case.get("case_id", "")).strip()
        response = responses.get(case_id)
        if not response:
            failures.append(f"{case_id}: missing response in report")
            print(f"[missing] {case_id}")
            continue

        review_status = str(case.get("review_status", "")).strip()
        checks = _check_case(response, case)
        if review_status == "approved":
            approved_total += 1
        else:
            followup_total += 1

        status = "ok" if not checks else "needs-review"
        print(
            f"[{status}] {case_id} | mode={response.get('response_mode', '-')}"
            f" | review={review_status} | failure_hint={response.get('failure_stage_hint', '-')}"
        )
        for check in checks:
            print(f"  - {check}")
        if review_status == "approved" and checks:
            failures.append(f"{case_id}: {', '.join(checks)}")

    print()
    print(f"Approved cases: {approved_total}")
    print(f"Follow-up cases: {followup_total}")

    if failures:
        print("Benchmark alignment failed:")
        for failure in failures:
            print(f" - {failure}")
        sys.exit(1)

    print("Benchmark alignment passed.")


if __name__ == "__main__":
    main()
