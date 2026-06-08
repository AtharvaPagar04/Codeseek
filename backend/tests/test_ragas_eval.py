import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

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


if __name__ == "__main__":
    unittest.main()
