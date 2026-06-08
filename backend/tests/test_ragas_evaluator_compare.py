import unittest
import tempfile
import json
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure backend directory is in path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from evals.ragas_evaluator_compare import parse_evaluator_config, main

class MockProcess:
    def __init__(self, returncode=0, stdout="", stderr="", write_report_fn=None, simulate_running=True):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.write_report_fn = write_report_fn
        self.simulate_running = simulate_running
        self._poll_count = 0

    def poll(self):
        self._poll_count += 1
        if self.simulate_running and self._poll_count == 1:
            return None
        if self.write_report_fn:
            self.write_report_fn()
        return self.returncode

    def communicate(self, timeout=None):
        return self.stdout, self.stderr

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        pass

class TestRagasEvaluatorCompare(unittest.TestCase):
    def test_parses_evaluator_config(self):
        provider, model, embedding = parse_evaluator_config("ollama:qwen2.5-coder:3b:nomic-embed-text")
        self.assertEqual(provider, "ollama")
        self.assertEqual(model, "qwen2.5-coder:3b")
        self.assertEqual(embedding, "nomic-embed-text")

        provider, model, embedding = parse_evaluator_config("openai:gpt-4o-mini:text-embedding-3-small")
        self.assertEqual(provider, "openai")
        self.assertEqual(model, "gpt-4o-mini")
        self.assertEqual(embedding, "text-embedding-3-small")

    def test_rejects_invalid_evaluator_config(self):
        with self.assertRaises(ValueError):
            parse_evaluator_config("invalid_format")
        with self.assertRaises(ValueError):
            parse_evaluator_config("gcp:vertex-ai:embedding")

    @patch("time.sleep", return_value=None)
    @patch("subprocess.Popen")
    def test_workflow_aggregates_correctly(self, mock_popen, mock_sleep):
        original_popen = subprocess.Popen
        def popen_side_effect(cmd, **kwargs):
            if not (isinstance(cmd, list) and len(cmd) > 1 and "ragas_eval.py" in cmd[1]):
                return original_popen(cmd, **kwargs)
            
            # Find the output path parameter
            out_path = None
            for idx, arg in enumerate(cmd):
                if arg == "--output":
                    out_path = Path(cmd[idx + 1])
                    break
            
            # If this is the first evaluator (pass), write the report
            if "qwen2.5-coder:3b" in cmd:
                def write_report():
                    if out_path:
                        report = {
                            "status": "PASS",
                            "score_health": {
                                "numeric_score_count": 3,
                                "null_score_count": 0,
                                "metrics_with_numeric_scores": ["answer_relevancy", "context_precision", "faithfulness"],
                                "metrics_with_null_scores": []
                            },
                            "metrics_run": ["answer_relevancy", "context_precision", "faithfulness"],
                            "metrics_skipped": {},
                            "errors": [],
                            "summary": {
                                "score_health": {},
                                "answer_relevancy": 0.9,
                                "context_precision": 0.0,
                                "faithfulness": 0.85
                            },
                            "traces": [
                                {
                                    "trace_id": "t1",
                                    "scores": {
                                        "answer_relevancy": 0.9,
                                        "context_precision": 0.0,
                                        "faithfulness": 0.85
                                    }
                                }
                            ]
                        }
                        with out_path.open("w", encoding="utf-8") as f:
                            json.dump(report, f)
                return MockProcess(returncode=0, stdout="Success", stderr="", write_report_fn=write_report, simulate_running=True)
            else:
                # Second evaluator (error)
                return MockProcess(returncode=1, stdout="", stderr="Mocked error message", simulate_running=True)

        mock_popen.side_effect = popen_side_effect

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            traces_path = tmp_path / "traces.jsonl"
            output_json = tmp_path / "compare_report.json"
            output_md = tmp_path / "compare_report.md"

            # Create dummy traces file
            with open(traces_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"trace_id": "t1", "question": "q", "answer": "a", "retrieved_contexts": [{"content": "some text"}]}) + "\n")

            cli_args = [
                "ragas_evaluator_compare.py",
                "--input-traces", str(traces_path),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
                "--metrics", "answer_relevancy,context_precision,faithfulness",
                "--evaluator", "ollama:qwen2.5-coder:3b:nomic-embed-text",
                "--evaluator", "ollama:qwen-coder-7b-16k:nomic-embed-text",
                "--verbose"
            ]

            with patch("sys.argv", cli_args):
                main()

            # Verify subprocess call args
            ragas_eval_calls = [
                call for call in mock_popen.call_args_list
                if isinstance(call[0][0], list) and len(call[0][0]) > 1 and "ragas_eval.py" in call[0][0][1]
            ]
            self.assertEqual(len(ragas_eval_calls), 2)
            
            cmd1 = ragas_eval_calls[0][0][0]
            self.assertEqual(cmd1[0], sys.executable)
            self.assertEqual(cmd1[1], "evals/ragas_eval.py")
            self.assertEqual(cmd1[2], "--input")
            self.assertEqual(cmd1[3], str(traces_path))
            self.assertEqual(cmd1[4], "--output")
            self.assertEqual(cmd1[6], "--evaluator-provider")
            self.assertEqual(cmd1[7], "ollama")
            self.assertEqual(cmd1[8], "--evaluator-model")
            self.assertEqual(cmd1[9], "qwen2.5-coder:3b")

            self.assertTrue(output_json.exists())
            self.assertTrue(output_md.exists())

            # Verify JSON content
            with output_json.open("r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["status"], "PARTIAL")
            self.assertEqual(len(data["results"]), 2)

            res_pass = data["results"][0]
            res_fail = data["results"][1]

            self.assertEqual(res_pass["status"], "PASS")
            self.assertEqual(res_pass["score_health"]["numeric_score_count"], 3)
            self.assertEqual(res_pass["metric_averages"]["answer_relevancy"], 0.9)
            self.assertEqual(res_pass["metric_averages"]["context_precision"], 0.0)
            self.assertEqual(res_pass["metric_averages"]["faithfulness"], 0.85)
            self.assertFalse(res_pass["timed_out"])
            self.assertEqual(res_pass["timeout_seconds"], 3600) # default

            self.assertEqual(res_fail["status"], "ERROR")
            self.assertEqual(res_fail["return_code"], 1)
            self.assertFalse(res_fail["timed_out"])

            summary = data["summary"]
            self.assertEqual(summary["best_numeric_score_health"], "ollama_qwen2_5_coder_3b_nomic_embed_text")
            self.assertEqual(summary["lowest_null_score_count"], "ollama_qwen2_5_coder_3b_nomic_embed_text")
            self.assertIn("context_precision remains 0.0 across all evaluators", summary["recommendation"])
            self.assertIn("Faithfulness null scores occurred on smaller local models", summary["recommendation"])

            # Verify Markdown content
            with output_md.open("r", encoding="utf-8") as f:
                md_content = f.read()
            self.assertIn("## Suggested Next Command", md_content)
            self.assertIn("evals/ragas_calibration.py", md_content)

    @patch("time.sleep", return_value=None)
    @patch("subprocess.Popen")
    def test_workflow_timeout(self, mock_popen, mock_sleep):
        original_popen = subprocess.Popen
        def popen_side_effect(cmd, **kwargs):
            if not (isinstance(cmd, list) and len(cmd) > 1 and "ragas_eval.py" in cmd[1]):
                return original_popen(cmd, **kwargs)
            return MockProcess(returncode=0, stdout="", stderr="", simulate_running=True)
        
        mock_popen.side_effect = popen_side_effect

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            traces_path = tmp_path / "traces.jsonl"
            output_json = tmp_path / "compare_report.json"
            output_md = tmp_path / "compare_report.md"

            # Create dummy traces file
            with open(traces_path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"trace_id": "t1", "question": "q", "answer": "a", "retrieved_contexts": [{"content": "some text"}]}) + "\n")

            cli_args = [
                "ragas_evaluator_compare.py",
                "--input-traces", str(traces_path),
                "--output-json", str(output_json),
                "--output-md", str(output_md),
                "--metrics", "answer_relevancy",
                "--evaluator", "ollama:qwen2.5-coder:3b:nomic-embed-text",
                "--evaluator", "openai:gpt-4o-mini:text-embedding-3-small",
                "--subprocess-timeout", "0"  # Force instant timeout
            ]

            with patch("sys.argv", cli_args):
                main()

            # Verify JSON content
            with output_json.open("r", encoding="utf-8") as f:
                data = json.load(f)

            # Both should have timed out, so top-level status is ERROR
            self.assertEqual(data["status"], "ERROR")
            self.assertEqual(len(data["results"]), 2)

            for result in data["results"]:
                self.assertEqual(result["status"], "ERROR")
                self.assertIsNone(result["return_code"])
                self.assertTrue(result["timed_out"])
                self.assertEqual(result["timeout_seconds"], 0)
                
                # Check for SUBPROCESS_TIMEOUT error type
                timeout_errors = [err for err in result["errors"] if err.get("type") == "SUBPROCESS_TIMEOUT"]
                self.assertEqual(len(timeout_errors), 1)
                self.assertEqual(timeout_errors[0]["timeout_seconds"], 0)

if __name__ == "__main__":
    unittest.main()
