import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_ragas_metrics


class CheckRagasMetricsTests(unittest.TestCase):
    def test_metric_average_and_low_score_count(self) -> None:
        report = {
            "summary": {
                "metric_averages": {
                    "context_precision": 0.9,
                    "context_recall": 0.88,
                    "faithfulness": 0.91,
                    "answer_relevancy": 0.87,
                    "answer_correctness": 0.84,
                }
            },
            "responses": [
                {
                    "ragas": {
                        "context_precision": {"state": "numeric", "value": 0.6},
                        "context_recall": {"state": "numeric", "value": 0.9},
                        "faithfulness": {"state": "numeric", "value": 0.92},
                        "answer_relevancy": {"state": "numeric", "value": 0.95},
                        "answer_correctness": {"state": "numeric", "value": 0.82},
                    }
                }
            ],
        }

        self.assertEqual(check_ragas_metrics._metric_average(report, "context_precision"), 0.9)
        self.assertEqual(check_ragas_metrics._count_low_scores(report, "context_precision", 0.7), 1)
        self.assertEqual(check_ragas_metrics._count_low_scores(report, "context_recall", 0.7), 0)

    def test_checker_runs_against_report_file(self) -> None:
        report = {
            "summary": {
                "metric_averages": {
                    "context_precision": 0.9,
                    "context_recall": 0.9,
                    "faithfulness": 0.92,
                    "answer_relevancy": 0.91,
                    "answer_correctness": 0.85,
                }
            },
            "responses": [
                {
                    "ragas": {
                        "context_precision": {"state": "numeric", "value": 0.9},
                        "context_recall": {"state": "numeric", "value": 0.9},
                        "faithfulness": {"state": "numeric", "value": 0.92},
                        "answer_relevancy": {"state": "numeric", "value": 0.91},
                        "answer_correctness": {"state": "numeric", "value": 0.85},
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            parsed = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(check_ragas_metrics._metric_average(parsed, "faithfulness"), 0.92)


if __name__ == "__main__":
    unittest.main()
