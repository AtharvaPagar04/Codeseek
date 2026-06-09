import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import sys
import importlib

# Ensure real tiktoken is loaded if a fake one was put into sys.modules by other tests
if "tiktoken" in sys.modules:
    tok = sys.modules["tiktoken"]
    if not hasattr(tok, "Encoding") or getattr(tok, "__name__", "") != "tiktoken":
        sys.modules.pop("tiktoken", None)
        try:
            importlib.import_module("tiktoken")
        except ImportError:
            pass

from evals import ragas_eval


class RagasEvalTests(unittest.TestCase):
    def test_load_answer_traces_valid_and_invalid(self) -> None:
        valid_trace_1 = {
            "trace_id": "trace-1",
            "question": "What is unit testing?",
            "answer": "A way to test units of code.",
            "retrieved_contexts": [
                {"content": "Unit testing is a software testing method..."}
            ],
            "ragas": {
                "question": "What is unit testing?",
                "answer": "A way to test units of code.",
                "contexts": ["Unit testing is a software testing method..."],
                "ground_truth": "Testing individual units of source code.",
            },
        }

        # Valid trace 2 with no ragas section but has top-level fields
        valid_trace_2 = {
            "trace_id": "trace-2",
            "question": "Is this Python?",
            "answer": "Yes.",
            "retrieved_contexts": [{"content": "Python is a programming language."}],
        }

        # Invalid trace (missing question)
        invalid_trace = {
            "trace_id": "trace-3",
            "answer": "No question here.",
            "retrieved_contexts": [{"content": "Just content."}],
        }

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "traces.jsonl"
            with tmp_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(valid_trace_1) + "\n")
                f.write("\n")  # Blank line
                f.write("invalid-json-line\n")  # Invalid JSON
                f.write(json.dumps(valid_trace_2) + "\n")
                f.write(json.dumps(invalid_trace) + "\n")

            valid_traces, errors, skipped = ragas_eval.load_answer_traces(
                tmp_path
            )

            self.assertEqual(len(valid_traces), 2)
            self.assertEqual(valid_traces[0]["trace_id"], "trace-1")
            self.assertEqual(valid_traces[1]["trace_id"], "trace-2")

            self.assertEqual(len(errors), 1)
            self.assertTrue("Invalid JSON" in errors[0])

            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["trace_id"], "trace-3")
            self.assertTrue("missing question" in skipped[0]["reason"])

    def test_trace_to_ragas_sample(self) -> None:
        trace_with_ragas = {
            "trace_id": "t1",
            "question": "Q",
            "answer": "A",
            "retrieved_contexts": [{"content": "C1"}],
            "ragas": {
                "question": "RQ",
                "answer": "RA",
                "contexts": ["RC1"],
                "ground_truth": "RG",
            },
        }
        sample_1 = ragas_eval.trace_to_ragas_sample(trace_with_ragas)
        self.assertEqual(sample_1["question"], "RQ")
        self.assertEqual(sample_1["answer"], "RA")
        self.assertEqual(sample_1["contexts"], ["RC1"])
        self.assertEqual(sample_1["ground_truth"], "RG")

        trace_without_ragas = {
            "trace_id": "t2",
            "question": "Q",
            "answer": "A",
            "retrieved_contexts": [{"content": "C1"}],
        }
        sample_2 = ragas_eval.trace_to_ragas_sample(trace_without_ragas)
        self.assertEqual(sample_2["question"], "Q")
        self.assertEqual(sample_2["answer"], "A")
        self.assertEqual(sample_2["contexts"], ["C1"])
        self.assertIsNone(sample_2["ground_truth"])

    def test_compute_diagnostics(self) -> None:
        sample = {
            "question": "Q",
            "answer": "See backend/retrieval/main.py for details.",
            "contexts": ["Context string here."],
        }
        trace = {
            "retrieved_contexts": [
                {"relative_path": "backend/retrieval/main.py"}
            ]
        }
        diags = ragas_eval.compute_diagnostics(sample, trace)
        self.assertEqual(diags["answer_length_chars"], len(sample["answer"]))
        self.assertEqual(diags["context_count"], 1)
        self.assertEqual(diags["total_context_chars"], len("Context string here."))
        self.assertTrue(diags["answer_has_citation_like_path"])
        self.assertTrue(diags["answer_mentions_top_context_file"])

    @patch("evals.ragas_eval.sys.exit")
    def test_dry_run_report_generation(self, mock_exit: MagicMock) -> None:
        trace = {
            "trace_id": "trace-dry",
            "question": "dry question",
            "answer": "dry answer",
            "retrieved_contexts": [{"content": "dry context"}],
        }
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            # Mock args
            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--dry-run",
                ],
            ):
                ragas_eval.main()

            self.assertTrue(output_path.exists())
            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["status"], "DRY_RUN_PASS")
            self.assertEqual(report["total_traces_loaded"], 1)
            self.assertEqual(report["total_traces_evaluated"], 1)
            self.assertEqual(len(report["traces"]), 1)
            self.assertEqual(report["traces"][0]["trace_id"], "trace-dry")
            self.assertIsNone(report["traces"][0]["scores"]["faithfulness"])

            # Verify score_health
            score_health = report["score_health"]
            self.assertEqual(score_health["numeric_score_count"], 0)
            self.assertEqual(score_health["null_score_count"], 3)
            self.assertEqual(score_health["metrics_with_numeric_scores"], [])
            self.assertEqual(
                score_health["metrics_with_null_scores"],
                ["answer_relevancy", "context_precision", "faithfulness"]
            )

    @patch("evals.ragas_eval.sys.exit")
    @patch("evals.ragas_eval.evaluate")
    @patch("evals.ragas_eval.RAGAS_AVAILABLE", True)
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-dummykey"})
    def test_live_run_all_null_scores_result_in_error(
        self, mock_evaluate: MagicMock, mock_exit: MagicMock
    ) -> None:
        trace = {
            "trace_id": "trace-failed-live",
            "question": "live question",
            "answer": "live answer",
            "retrieved_contexts": [{"content": "live context"}],
        }

        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "faithfulness": [None],
                "answer_relevancy": [None],
                "context_precision": [None],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df
        mock_evaluate.return_value = mock_result

        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--allow-no-ground-truth",
                ],
            ):
                ragas_eval.main()

            self.assertTrue(output_path.exists())
            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["status"], "ERROR")
            self.assertEqual(report["total_traces_loaded"], 1)
            self.assertEqual(report["total_traces_evaluated"], 1)

            # Check error list
            has_no_numeric_err = any(
                isinstance(err, dict) and err.get("type") == "NO_NUMERIC_RAGAS_SCORES"
                for err in report["errors"]
            )
            self.assertTrue(has_no_numeric_err)

            # Verify score_health
            score_health = report["score_health"]
            self.assertEqual(score_health["numeric_score_count"], 0)
            self.assertEqual(score_health["null_score_count"], 3)
            self.assertEqual(score_health["metrics_with_numeric_scores"], [])
            self.assertEqual(
                score_health["metrics_with_null_scores"],
                ["answer_relevancy", "context_precision", "faithfulness"]
            )

    @patch("evals.ragas_eval.sys.exit")
    @patch("evals.ragas_eval.evaluate")
    @patch("evals.ragas_eval.RAGAS_AVAILABLE", True)
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-dummykey"})
    def test_live_run_mixed_scores_result_in_partial(
        self, mock_evaluate: MagicMock, mock_exit: MagicMock
    ) -> None:
        trace = {
            "trace_id": "trace-mixed-live",
            "question": "live question",
            "answer": "live answer",
            "retrieved_contexts": [{"content": "live context"}],
        }

        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.8],
                "answer_relevancy": [None],
                "context_precision": [0.9],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df
        mock_evaluate.return_value = mock_result

        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--allow-no-ground-truth",
                ],
            ):
                ragas_eval.main()

            self.assertTrue(output_path.exists())
            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["status"], "PARTIAL")

            # Verify score_health
            score_health = report["score_health"]
            self.assertEqual(score_health["numeric_score_count"], 2)
            self.assertEqual(score_health["null_score_count"], 1)
            self.assertEqual(
                score_health["metrics_with_numeric_scores"],
                ["context_precision", "faithfulness"]
            )
            self.assertEqual(
                score_health["metrics_with_null_scores"],
                ["answer_relevancy"]
            )

    @patch("evals.ragas_eval.sys.exit")
    @patch("evals.ragas_eval.evaluate")
    @patch("evals.ragas_eval.RAGAS_AVAILABLE", True)
    @patch.dict("os.environ", {"OPENAI_API_KEY": "sk-dummykey"})
    def test_live_run_all_numeric_scores_result_in_pass(
        self, mock_evaluate: MagicMock, mock_exit: MagicMock
    ) -> None:
        trace = {
            "trace_id": "trace-pass-live",
            "question": "live question",
            "answer": "live answer",
            "retrieved_contexts": [{"content": "live context"}],
        }

        import pandas as pd

        mock_df = pd.DataFrame(
            {
                "faithfulness": [0.8],
                "answer_relevancy": [0.75],
                "context_precision": [0.9],
            }
        )
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = mock_df
        mock_evaluate.return_value = mock_result

        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--allow-no-ground-truth",
                ],
            ):
                ragas_eval.main()

            self.assertTrue(output_path.exists())
            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["status"], "PASS")

            # Verify score_health
            score_health = report["score_health"]
            self.assertEqual(score_health["numeric_score_count"], 3)
            self.assertEqual(score_health["null_score_count"], 0)
            self.assertEqual(
                score_health["metrics_with_numeric_scores"],
                ["answer_relevancy", "context_precision", "faithfulness"]
            )
            self.assertEqual(score_health["metrics_with_null_scores"], [])

    @patch("urllib.request.urlopen")
    def test_ollama_health_check_unreachable(self, mock_urlopen: MagicMock) -> None:
        # Mock connection failure
        mock_urlopen.side_effect = Exception("Connection refused")
        ok, errors = ragas_eval.check_ollama_health(
            "http://localhost:11434", "qwen2.5-coder:3b", "nomic-embed-text"
        )
        self.assertFalse(ok)
        self.assertTrue(any("Connection refused" in err for err in errors))

    @patch("urllib.request.urlopen")
    def test_ollama_health_check_missing_model(self, mock_urlopen: MagicMock) -> None:
        # Mock response from Ollama
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "models": [
                {"name": "llama3.1:8b"}
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        ok, errors = ragas_eval.check_ollama_health(
            "http://localhost:11434", "qwen2.5-coder:3b", "nomic-embed-text"
        )
        self.assertFalse(ok)
        self.assertTrue(any("Missing evaluator model 'qwen2.5-coder:3b'" in err for err in errors))
        self.assertTrue(any("Missing embedding model 'nomic-embed-text'" in err for err in errors))

    @patch("urllib.request.urlopen")
    def test_ollama_health_check_success_and_matching(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps({
            "models": [
                {"name": "qwen2.5-coder:3b-5k"},
                {"name": "nomic-embed-text:latest"}
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        ok, errors = ragas_eval.check_ollama_health(
            "http://localhost:11434", "qwen2.5-coder:3b", "nomic-embed-text"
        )
        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)

    @patch("evals.ragas_eval.sys.exit")
    @patch("evals.ragas_eval.check_ollama_health")
    def test_dry_run_with_health_check(self, mock_health: MagicMock, mock_exit: MagicMock) -> None:
        # 1. Successful health check
        mock_health.return_value = (True, [])
        trace = {
            "trace_id": "trace-dry-health",
            "question": "dry question",
            "answer": "dry answer",
            "retrieved_contexts": [{"content": "dry context"}],
        }
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input", str(input_path),
                    "--output", str(output_path),
                    "--dry-run",
                    "--evaluator-provider", "ollama",
                    "--evaluator-model", "qwen2.5-coder:3b",
                    "--embedding-model", "nomic-embed-text",
                    "--check-evaluator-health",
                ],
            ):
                ragas_eval.main()

            mock_health.assert_called_once_with(
                base_url="http://localhost:11434",
                model="qwen2.5-coder:3b",
                embedding_model="nomic-embed-text"
            )

            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["status"], "DRY_RUN_PASS")
            self.assertEqual(report["evaluator"]["provider"], "ollama")
            self.assertEqual(report["evaluator"]["model"], "qwen2.5-coder:3b")
            self.assertEqual(report["evaluator"]["embedding_model"], "nomic-embed-text")
            self.assertEqual(report["evaluator"]["base_url"], "http://localhost:11434")
            self.assertIn("runtime", report)
            self.assertTrue(report["runtime"]["langchain_ollama_available"])

        # 2. Failing health check
        mock_health.reset_mock()
        mock_health.return_value = (False, ["Ollama unreachable", "Missing model: nomic-embed-text"])
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input", str(input_path),
                    "--output", str(output_path),
                    "--dry-run",
                    "--evaluator-provider", "ollama",
                    "--check-evaluator-health",
                ],
            ):
                ragas_eval.main()

            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["status"], "ERROR")
            has_health_errors = any(
                isinstance(err, dict) and err.get("type") == "OLLAMA_HEALTH_CHECK_FAILED"
                for err in report["errors"]
            )
            self.assertTrue(has_health_errors)

    @patch("evals.ragas_eval.sys.exit")
    @patch("evals.ragas_eval.check_ollama_health")
    def test_dry_run_without_health_check_does_not_call_health(self, mock_health: MagicMock, mock_exit: MagicMock) -> None:
        trace = {
            "trace_id": "trace-dry-no-health",
            "question": "dry question",
            "answer": "dry answer",
            "retrieved_contexts": [{"content": "dry context"}],
        }
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input", str(input_path),
                    "--output", str(output_path),
                    "--dry-run",
                    "--evaluator-provider", "ollama",
                ],
            ):
                ragas_eval.main()

            mock_health.assert_not_called()
            
            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual(report["status"], "DRY_RUN_PASS")

    def test_parse_metric_names_none(self) -> None:
        valid, invalid = ragas_eval.parse_metric_names(None)
        self.assertEqual(valid, ragas_eval.DEFAULT_RAGAS_METRICS)
        self.assertEqual(invalid, [])

    def test_parse_metric_names_single(self) -> None:
        valid, invalid = ragas_eval.parse_metric_names("answer_relevancy")
        self.assertEqual(valid, ["answer_relevancy"])
        self.assertEqual(invalid, [])

    def test_parse_metric_names_multiple(self) -> None:
        valid, invalid = ragas_eval.parse_metric_names("faithfulness,answer_relevancy")
        self.assertEqual(valid, ["faithfulness", "answer_relevancy"])
        self.assertEqual(invalid, [])

    def test_parse_metric_names_invalid(self) -> None:
        valid, invalid = ragas_eval.parse_metric_names("faithfulness,fake_metric")
        self.assertEqual(valid, ["faithfulness"])
        self.assertEqual(invalid, ["fake_metric"])

    @patch("evals.ragas_eval.sys.exit", side_effect=SystemExit)
    def test_invalid_metric_writes_error_report(self, mock_exit: MagicMock) -> None:
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"

            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"question": "q", "answer": "a", "retrieved_contexts": []}) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input", str(input_path),
                    "--output", str(output_path),
                    "--metrics", "fake_metric",
                    "--dry-run"
                ]
            ):
                with self.assertRaises(SystemExit):
                    ragas_eval.main()

            self.assertTrue(output_path.exists())
            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["status"], "ERROR")
            self.assertEqual(report["errors"][0]["type"], "INVALID_RAGAS_METRICS")
            self.assertIn("fake_metric", report["errors"][0]["message"])

    @patch("evals.ragas_eval.sys.exit")
    def test_ollama_default_runtime_configuration(self, mock_exit: MagicMock) -> None:
        trace = {
            "trace_id": "t1",
            "question": "q",
            "answer": "a",
            "retrieved_contexts": [{"content": "c"}]
        }
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"
            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input", str(input_path),
                    "--output", str(output_path),
                    "--evaluator-provider", "ollama",
                    "--dry-run"
                ]
            ):
                ragas_eval.main()

            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["ragas_runtime"]["timeout"], 600)
            self.assertEqual(report["ragas_runtime"]["max_workers"], 1)
            self.assertEqual(report["ragas_runtime"]["max_retries"], 1)

    @patch("evals.ragas_eval.sys.exit")
    def test_openai_default_runtime_configuration(self, mock_exit: MagicMock) -> None:
        trace = {
            "trace_id": "t1",
            "question": "q",
            "answer": "a",
            "retrieved_contexts": [{"content": "c"}]
        }
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"
            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input", str(input_path),
                    "--output", str(output_path),
                    "--evaluator-provider", "openai",
                    "--dry-run"
                ]
            ):
                ragas_eval.main()

            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["ragas_runtime"]["timeout"], 180)
            self.assertEqual(report["ragas_runtime"]["max_workers"], 4)
            self.assertEqual(report["ragas_runtime"]["max_retries"], 3)

    @patch("evals.ragas_eval.sys.exit")
    def test_dry_run_report_includes_ragas_runtime(self, mock_exit: MagicMock) -> None:
        trace = {
            "trace_id": "t1",
            "question": "q",
            "answer": "a",
            "retrieved_contexts": [{"content": "c"}]
        }
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.jsonl"
            output_path = Path(tmp) / "output.json"
            with input_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(trace) + "\n")

            with patch(
                "sys.argv",
                [
                    "ragas_eval.py",
                    "--input", str(input_path),
                    "--output", str(output_path),
                    "--dry-run",
                    "--ragas-timeout", "99",
                    "--ragas-max-workers", "2",
                    "--ragas-max-retries", "5",
                    "--metrics", "answer_relevancy"
                ]
            ):
                ragas_eval.main()

            with output_path.open("r", encoding="utf-8") as f:
                report = json.load(f)

            self.assertEqual(report["ragas_runtime"]["timeout"], 99)
            self.assertEqual(report["ragas_runtime"]["max_workers"], 2)
            self.assertEqual(report["ragas_runtime"]["max_retries"], 5)
            self.assertEqual(report["ragas_runtime"]["metrics_requested_raw"], "answer_relevancy")
            self.assertEqual(report["ragas_runtime"]["metrics_selected"], ["answer_relevancy"])


if __name__ == "__main__":
    unittest.main()
