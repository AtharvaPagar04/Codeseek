"""Load RAGAS validation artifacts for the frontend scorecard UI."""

from __future__ import annotations

import json
from pathlib import Path

from retrieval.ragas_eval_support import compare_family_baselines

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs" / "retrieval_docs"
LATEST_REPORT_PATH = DOCS_DIR / "eval_results_ragas_latest.json"
LATEST_MARKDOWN_PATH = DOCS_DIR / "eval_results_ragas_latest.md"
FAMILY_BASELINE_PATH = DOCS_DIR / "ragas_family_baseline_latest.json"
HUMAN_REVIEW_PATH = DOCS_DIR / "ragas_human_review_benchmark_v1.json"


def artifact_paths() -> dict[str, str]:
    return {
        "report": str(LATEST_REPORT_PATH),
        "report_markdown": str(LATEST_MARKDOWN_PATH),
        "family_baseline": str(FAMILY_BASELINE_PATH),
        "human_review_benchmark": str(HUMAN_REVIEW_PATH),
    }


def load_json_artifact(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_ragas_validation_bundle() -> dict:
    report = load_json_artifact(LATEST_REPORT_PATH)
    family_baseline = load_json_artifact(FAMILY_BASELINE_PATH)
    human_review_benchmark = load_json_artifact(HUMAN_REVIEW_PATH)
    family_baseline_trend = None
    if report and family_baseline:
        family_baseline_trend = compare_family_baselines(report, family_baseline)
    return {
        "artifacts": {
            "paths": artifact_paths(),
            "report_exists": LATEST_REPORT_PATH.exists(),
            "family_baseline_exists": FAMILY_BASELINE_PATH.exists(),
            "human_review_benchmark_exists": HUMAN_REVIEW_PATH.exists(),
            "report_markdown_exists": LATEST_MARKDOWN_PATH.exists(),
        },
        "report": report,
        "family_baseline": family_baseline,
        "family_baseline_trend": family_baseline_trend,
        "human_review_benchmark": human_review_benchmark,
    }
