import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_ragas_human_review


class CheckRagasHumanReviewTests(unittest.TestCase):
    def test_metric_value_and_mode_match(self) -> None:
        response = {
            "response_mode": "llm",
            "ragas": {
                "faithfulness": {"state": "numeric", "value": 0.92},
                "context_precision": {"state": "error"},
            },
        }

        self.assertEqual(check_ragas_human_review._metric_value(response, "faithfulness"), ("numeric", 0.92))
        self.assertEqual(check_ragas_human_review._metric_value(response, "context_precision"), ("error", 0.0))
        self.assertTrue(check_ragas_human_review._matches_expected_mode(response, "llm"))
        self.assertFalse(check_ragas_human_review._matches_expected_mode(response, "flow_summary"))

    def test_check_case_flags_threshold_violations(self) -> None:
        response = {
            "response_mode": "llm",
            "ragas": {
                "context_precision": {"state": "numeric", "value": 0.6},
                "context_recall": {"state": "numeric", "value": 0.8},
                "faithfulness": {"state": "numeric", "value": 0.7},
                "answer_relevancy": {"state": "numeric", "value": 0.9},
                "answer_correctness": {"state": "numeric", "value": 0.8},
            },
        }
        benchmark_case = {
            "case_id": "cs-ragas-001",
            "review_status": "approved",
            "expected_response_mode": "llm",
            "minimums": {"context_precision": 0.7, "faithfulness": 0.8},
        }

        failures = check_ragas_human_review._check_case(response, benchmark_case)

        self.assertEqual(len(failures), 2)
        self.assertTrue(any("context_precision" in item for item in failures))
        self.assertTrue(any("faithfulness" in item for item in failures))

    def test_script_parses_report_and_benchmark_files(self) -> None:
        report = {
            "run_meta": {"dataset_name": "demo"},
            "responses": [
                {
                    "case_id": "cs-ragas-001",
                    "response_mode": "llm",
                    "failure_stage_hint": "none",
                    "ragas": {
                        "context_precision": {"state": "numeric", "value": 0.8},
                        "context_recall": {"state": "numeric", "value": 0.8},
                        "faithfulness": {"state": "numeric", "value": 0.9},
                        "answer_relevancy": {"state": "numeric", "value": 0.85},
                        "answer_correctness": {"state": "numeric", "value": 0.8},
                    },
                }
            ],
        }
        benchmark = {
            "name": "demo-benchmark",
            "cases": [
                {
                    "case_id": "cs-ragas-001",
                    "review_status": "approved",
                    "expected_response_mode": "llm",
                    "minimums": {"faithfulness": 0.8},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            benchmark_path = Path(tmp) / "benchmark.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")
            loaded_report = json.loads(report_path.read_text(encoding="utf-8"))
            loaded_benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))

        self.assertEqual(loaded_report["run_meta"]["dataset_name"], "demo")
        self.assertEqual(loaded_benchmark["name"], "demo-benchmark")


if __name__ == "__main__":
    unittest.main()
