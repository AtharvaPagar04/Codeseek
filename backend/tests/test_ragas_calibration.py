import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import yaml

from evals.ragas_calibration import (
    load_calibration_queries,
    compute_calibration_diagnostics,
    interpret_result
)

class TestRagasCalibration(unittest.TestCase):
    def test_load_calibration_yaml(self):
        content = {
            "queries": [
                {
                    "id": "q_test",
                    "query": "Test query?",
                    "category": "test",
                    "expected_files": ["backend/test.py"],
                    "expected_answer_contains": ["test", "pass"]
                }
            ]
        }
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as tmp:
            yaml.dump(content, tmp)
            tmp_path = Path(tmp.name)
        try:
            queries = load_calibration_queries(tmp_path)
            self.assertEqual(len(queries), 1)
            self.assertEqual(queries[0]["id"], "q_test")
            self.assertEqual(queries[0]["query"], "Test query?")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_deterministic_diagnostics(self):
        query = {
            "id": "q_test",
            "query": "Test query?",
            "expected_files": ["backend/test.py"],
            "expected_answer_contains": ["hello", "world"]
        }
        trace = {
            "answer": "This is a hello world answer that mentions backend/test.py",
            "retrieved_contexts": [
                {
                    "relative_path": "backend/test.py",
                    "content": "some content here"
                }
            ]
        }
        diags = compute_calibration_diagnostics(query, trace)
        self.assertEqual(diags["answer_length_chars"], len(trace["answer"]))
        self.assertEqual(diags["context_count"], 1)
        self.assertEqual(diags["total_context_chars"], len("some content here"))
        self.assertTrue(diags["expected_file_found_in_contexts"])
        self.assertTrue(diags["expected_answer_terms_found"]["hello"])
        self.assertTrue(diags["expected_answer_terms_found"]["world"])
        self.assertTrue(diags["answer_mentions_expected_file"])
        self.assertTrue(diags["answer_mentions_any_top_context_file"])

    def test_interpretation_rules(self):
        # 1. Short answer -> answer_too_short_for_ragas
        query = {
            "id": "q_test",
            "expected_files": ["backend/test.py"]
        }
        diags = {
            "answer_length_chars": 150,
            "context_count": 1,
            "total_context_chars": 100,
            "top_context_files": ["backend/test.py"],
            "expected_file_found_in_contexts": True,
            "answer_mentions_expected_file": True
        }
        scores = {"faithfulness": 0.0, "answer_relevancy": 0.0, "context_precision": 0.0}
        
        self.assertEqual(
            interpret_result(query, diags, scores, False),
            "answer_too_short_for_ragas"
        )
        
        # 2. Missing expected file -> retrieval_context_missing_expected_file
        diags_missing = {
            "answer_length_chars": 250,
            "context_count": 1,
            "total_context_chars": 100,
            "top_context_files": ["backend/other.py"],
            "expected_file_found_in_contexts": False,
            "answer_mentions_expected_file": False
        }
        self.assertEqual(
            interpret_result(query, diags_missing, scores, False),
            "retrieval_context_missing_expected_file"
        )
        
        # 3. Good context + zero scores -> retrieval_context_good_but_local_judge_low_score
        diags_good = {
            "answer_length_chars": 250,
            "context_count": 1,
            "total_context_chars": 100,
            "top_context_files": ["backend/test.py"],
            "expected_file_found_in_contexts": True,
            "answer_mentions_expected_file": True
        }
        self.assertEqual(
            interpret_result(query, diags_good, scores, False),
            "retrieval_context_good_but_local_judge_low_score"
        )
        
        # 4. Low scores (< 0.5) but not all zero -> actual_answer_grounding_problems
        scores_low = {"faithfulness": 0.2, "answer_relevancy": 0.3, "context_precision": 0.1}
        self.assertEqual(
            interpret_result(query, diags_good, scores_low, False),
            "actual_answer_grounding_problems"
        )
        
        # 5. High scores -> calibrated_pass
        scores_high = {"faithfulness": 0.8, "answer_relevancy": 0.9, "context_precision": 0.8}
        self.assertEqual(
            interpret_result(query, diags_good, scores_high, False),
            "calibrated_pass"
        )
        
        # 6. RAGAS failed -> ragas_execution_failed
        self.assertEqual(
            interpret_result(query, diags_good, scores_high, True),
            "ragas_execution_failed"
        )

    @patch("retrieval.main.run_query")
    @patch("evals.ragas_eval.main")
    def test_calibration_cli_passes_arguments_and_preserves_runtime(self, mock_ragas_main: MagicMock, mock_run_query: MagicMock):
        content = {
            "queries": [
                {
                    "id": "q_test",
                    "query": "Test query?",
                    "category": "test",
                    "expected_files": ["backend/test.py"],
                    "expected_answer_contains": ["test"]
                }
            ]
        }
        
        from evals import ragas_calibration
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            queries_path = tmp_path / "queries.yaml"
            trace_output_path = tmp_path / "traces.jsonl"
            ragas_output_path = tmp_path / "ragas_output.json"
            summary_output_path = tmp_path / "summary.json"

            with open(queries_path, "w", encoding="utf-8") as f:
                yaml.dump(content, f)

            # Write a dummy trace to satisfy file existence check
            with open(trace_output_path, "w", encoding="utf-8") as tf:
                tf.write(json.dumps({
                    "trace_id": "q_test",
                    "question": "Test query?",
                    "answer": "Test answer",
                    "retrieved_contexts": [{"content": "Test context", "relative_path": "backend/test.py"}]
                }) + "\n")

            dummy_ragas_output = {
                "status": "PASS",
                "ragas_runtime": {
                    "timeout": 456,
                    "max_workers": 2,
                    "max_retries": 3,
                    "metrics_requested_raw": "answer_relevancy",
                    "metrics_selected": ["answer_relevancy"],
                    "run_config_available": True
                },
                "traces": [
                    {
                        "extra": {"query_id": "q_test"},
                        "question": "Test query?",
                        "scores": {
                            "faithfulness": 0.8,
                            "answer_relevancy": 0.9,
                            "context_precision": 0.8
                        }
                    }
                ]
            }

            captured_sys_argv = []
            def side_effect_ragas_main_capture():
                import sys
                captured_sys_argv.extend(sys.argv)
                with open(ragas_output_path, "w", encoding="utf-8") as rf:
                    json.dump(dummy_ragas_output, rf)

            mock_ragas_main.side_effect = side_effect_ragas_main_capture

            cli_args = [
                "ragas_calibration.py",
                "--queries", str(queries_path),
                "--trace-output", str(trace_output_path),
                "--ragas-output", str(ragas_output_path),
                "--summary-output", str(summary_output_path),
                "--provider", "ollama",
                "--ragas-timeout", "456",
                "--ragas-max-workers", "2",
                "--ragas-max-retries", "3",
                "--metrics", "answer_relevancy"
            ]

            with patch("sys.argv", cli_args):
                ragas_calibration.main()

            mock_ragas_main.assert_called_once()
            
            self.assertIn("--ragas-timeout", captured_sys_argv)
            self.assertIn("456", captured_sys_argv)
            self.assertIn("--ragas-max-workers", captured_sys_argv)
            self.assertIn("2", captured_sys_argv)
            self.assertIn("--ragas-max-retries", captured_sys_argv)
            self.assertIn("3", captured_sys_argv)
            self.assertIn("--metrics", captured_sys_argv)
            self.assertIn("answer_relevancy", captured_sys_argv)

            self.assertTrue(summary_output_path.exists())
            with open(summary_output_path, "r", encoding="utf-8") as sf:
                summary = json.load(sf)

            self.assertIn("ragas_runtime", summary)
            self.assertEqual(summary["ragas_runtime"]["timeout"], 456)
            self.assertEqual(summary["ragas_runtime"]["max_workers"], 2)
            self.assertEqual(summary["ragas_runtime"]["metrics_requested_raw"], "answer_relevancy")


if __name__ == "__main__":
    unittest.main()
