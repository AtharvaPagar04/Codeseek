import json
import tempfile
import unittest
from pathlib import Path

from retrieval import ragas_reports


class RagasReportsTests(unittest.TestCase):
    def test_load_ragas_validation_bundle_reads_available_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = Path(tmp) / "docs" / "retrieval_docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            report_path = docs_dir / "eval_results_ragas_latest.json"
            baseline_path = docs_dir / "ragas_family_baseline_latest.json"
            benchmark_path = docs_dir / "ragas_human_review_benchmark_v1.json"

            report_path.write_text(
                json.dumps(
                    {
                        "run_meta": {
                            "dataset_name": "demo",
                            "generated_at_utc": "2026-06-06T00:00:00Z",
                            "case_count": 1,
                        },
                        "responses": [
                            {
                                "primary_intent": "SYMBOL",
                                "response_mode": "llm",
                                "ragas": {
                                    "context_precision": {"state": "numeric", "value": 0.8},
                                    "context_recall": {"state": "numeric", "value": 0.7},
                                    "faithfulness": {"state": "numeric", "value": 0.9},
                                    "answer_relevancy": {"state": "numeric", "value": 0.85},
                                    "answer_correctness": {"state": "numeric", "value": 0.75},
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            baseline_path.write_text(
                json.dumps(
                    {
                        "source_report": {
                            "dataset_name": "baseline",
                            "generated_at_utc": "2026-06-05T00:00:00Z",
                            "case_count": 1,
                        },
                        "families": {
                            "primary_intent": {
                                "SYMBOL": {
                                    "count": 1,
                                    "metric_averages": {
                                        "context_precision": 0.5,
                                        "context_recall": 0.5,
                                        "faithfulness": 0.5,
                                        "answer_relevancy": 0.5,
                                        "answer_correctness": 0.5,
                                    },
                                }
                            },
                            "response_mode": {},
                        },
                    }
                ),
                encoding="utf-8",
            )
            benchmark_path.write_text(json.dumps({"name": "demo", "cases": []}), encoding="utf-8")

            original_docs = ragas_reports.DOCS_DIR
            original_report = ragas_reports.LATEST_REPORT_PATH
            original_baseline = ragas_reports.FAMILY_BASELINE_PATH
            original_benchmark = ragas_reports.HUMAN_REVIEW_PATH
            original_markdown = ragas_reports.LATEST_MARKDOWN_PATH
            bundle = None
            try:
                ragas_reports.DOCS_DIR = docs_dir
                ragas_reports.LATEST_REPORT_PATH = report_path
                ragas_reports.FAMILY_BASELINE_PATH = baseline_path
                ragas_reports.HUMAN_REVIEW_PATH = benchmark_path
                ragas_reports.LATEST_MARKDOWN_PATH = docs_dir / "eval_results_ragas_latest.md"

                bundle = ragas_reports.load_ragas_validation_bundle()
            finally:
                ragas_reports.DOCS_DIR = original_docs
                ragas_reports.LATEST_REPORT_PATH = original_report
                ragas_reports.FAMILY_BASELINE_PATH = original_baseline
                ragas_reports.HUMAN_REVIEW_PATH = original_benchmark
                ragas_reports.LATEST_MARKDOWN_PATH = original_markdown

        self.assertTrue(bundle["artifacts"]["report_exists"])
        self.assertTrue(bundle["artifacts"]["family_baseline_exists"])
        self.assertTrue(bundle["artifacts"]["human_review_benchmark_exists"])
        self.assertIsNotNone(bundle["family_baseline_trend"])
        self.assertEqual(bundle["report"]["run_meta"]["dataset_name"], "demo")


if __name__ == "__main__":
    unittest.main()
