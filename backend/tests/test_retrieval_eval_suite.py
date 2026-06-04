import unittest
from pathlib import Path

from scripts import retrieval_eval_suite


class RetrievalEvalSuiteTests(unittest.TestCase):
    def test_parse_eval_output_includes_automated_scores(self) -> None:
        output = """
Retrieval Eval Results
======================
Cases: 2
hit@10: 0.500
mrr@10: 0.250
citation_coverage: 0.750
expected_file_score: 1.000
expected_symbol_score: 0.500
expected_framework_score: 1.000
expected_dependency_score: 0.500
expected_no_answer_score: 1.000
expected_response_mode_score: 1.000
expected_answer_term_score: 0.750
latency_p50_ms: 123
latency_p95_ms: 456
"""

        metrics = retrieval_eval_suite._parse_eval_output(output)

        self.assertEqual(metrics["cases"], 2.0)
        self.assertEqual(metrics["hit"], 0.5)
        self.assertEqual(metrics["expected_file"], 1.0)
        self.assertEqual(metrics["expected_symbol"], 0.5)
        self.assertEqual(metrics["expected_framework"], 1.0)
        self.assertEqual(metrics["expected_dependency"], 0.5)
        self.assertEqual(metrics["expected_no_answer"], 1.0)
        self.assertEqual(metrics["expected_response_mode"], 1.0)
        self.assertEqual(metrics["expected_answer_term"], 0.75)
        self.assertEqual(metrics["latency_p50_ms"], 123.0)
        self.assertEqual(metrics["latency_p95_ms"], 456.0)

    def test_resolve_dataset_path_uses_project_root_for_relative_paths(self) -> None:
        project_root = Path("/repo/backend")

        self.assertEqual(
            retrieval_eval_suite._resolve_dataset_path(
                project_root, "tests/fixtures/retrieval_repos/frontend_app"
            ),
            project_root / "tests/fixtures/retrieval_repos/frontend_app",
        )
        self.assertEqual(
            retrieval_eval_suite._resolve_dataset_path(project_root, "/tmp/repo"),
            Path("/tmp/repo"),
        )


if __name__ == "__main__":
    unittest.main()
