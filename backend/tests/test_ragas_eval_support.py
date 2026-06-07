import json
import tempfile
import unittest
from pathlib import Path

from retrieval.ragas_eval_support import (
    build_family_baseline_snapshot,
    compute_metric_bundle,
    compare_family_baselines,
    infer_failure_stage_hint,
    load_fixture,
    render_markdown_report,
    serialize_context_block,
    serialize_source_item,
    summarize_entries,
)


class RagasEvalSupportTests(unittest.TestCase):
    def test_load_fixture_reads_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.json"
            path.write_text(
                json.dumps({"name": "demo", "cases": [{"id": "c1", "query": "q"}]}),
                encoding="utf-8",
            )

            fixture, cases = load_fixture(path)

        self.assertEqual(fixture["name"], "demo")
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "c1")

    def test_serializers_preserve_source_and_context_details(self) -> None:
        source = serialize_source_item(
            {
                "relative_path": "retrieval/searcher.py",
                "symbol_name": "_dense_search",
                "qualified_symbol": "retrieval/searcher.py::_dense_search",
                "chunk_type": "function",
                "start_line": 10,
                "end_line": 20,
                "expansion_type": "primary",
                "score": 0.9,
                "summary": "Dense search",
            }
        )
        block = serialize_context_block(
            {
                "text": "### retrieval/searcher.py",
                "relative_path": "retrieval/searcher.py",
                "symbol_name": "_dense_search",
                "chunk_type": "function",
                "start_line": 10,
                "end_line": 20,
                "expansion_type": "primary",
            }
        )

        self.assertEqual(source["symbol_name"], "_dense_search")
        self.assertEqual(block["relative_path"], "retrieval/searcher.py")
        self.assertEqual(block["text"], "### retrieval/searcher.py")

    def test_metric_bundle_scores_simple_grounded_case(self) -> None:
        question = "Where is _dense_search implemented?"
        answer = "The dense search path is implemented in retrieval/searcher.py::_dense_search."
        context = [
            {
                "text": "### retrieval/searcher.py — _dense_search",
                "relative_path": "retrieval/searcher.py",
                "symbol_name": "_dense_search",
            }
        ]
        metrics = compute_metric_bundle(
            question=question,
            answer=answer,
            answer_context_blocks=context,
            ground_truth="The dense search path is implemented in retrieval/searcher.py::_dense_search.",
            ground_truth_sources=[{"relative_path": "retrieval/searcher.py", "symbol_name": "_dense_search"}],
            response_mode="llm",
        )

        self.assertEqual(metrics["context_recall"].state, "numeric")
        self.assertGreater(metrics["context_recall"].value or 0.0, 0.9)
        self.assertGreater(metrics["faithfulness"].value or 0.0, 0.3)
        self.assertGreater(metrics["answer_relevancy"].value or 0.0, 0.2)
        self.assertEqual(metrics["answer_correctness"].state, "numeric")

    def test_failure_stage_hint_prefers_search_when_sources_missing(self) -> None:
        metrics = {
            "context_precision": type("Cell", (), {"value": 0.1})(),
            "context_recall": type("Cell", (), {"value": 0.1})(),
            "faithfulness": type("Cell", (), {"value": 0.1})(),
            "answer_relevancy": type("Cell", (), {"value": 0.1})(),
            "answer_correctness": type("Cell", (), {"value": 0.1})(),
        }
        hint = infer_failure_stage_hint(
            query="Where is _dense_search implemented?",
            response_mode="llm",
            expected_response_mode="llm",
            search_candidates=[],
            expanded_candidates=[],
            assembled_sources=[],
            display_sources=[],
            reasoning_sources=[],
            ground_truth_sources=[{"relative_path": "retrieval/searcher.py", "symbol_name": "_dense_search"}],
            metric_bundle=metrics,
        )
        self.assertEqual(hint, "search")

    def test_report_summary_and_markdown_render(self) -> None:
        report = {
            "run_meta": {
                "dataset_name": "demo",
                "repo_root": "/repo",
                "collection_name": "collection",
                "generated_at_utc": "2026-06-06T00:00:00Z",
                "case_count": 1,
            },
            "summary": summarize_entries(
                [
                    {
                        "case_id": "c1",
                        "query": "Where is _dense_search implemented?",
                        "response_mode": "llm",
                        "primary_intent": "SYMBOL",
                        "failure_stage_hint": "search",
                        "ragas": {
                            "context_precision": {"state": "numeric", "value": 0.5},
                            "context_recall": {"state": "numeric", "value": 0.75},
                            "faithfulness": {"state": "numeric", "value": 0.8},
                            "answer_relevancy": {"state": "numeric", "value": 0.6},
                            "answer_correctness": {"state": "numeric", "value": 0.7},
                        },
                    }
                ]
            ),
            "responses": [
                {
                    "case_id": "c1",
                    "query": "Where is _dense_search implemented?",
                    "response_mode": "llm",
                    "failure_stage_hint": "search",
                    "ragas": {
                        "context_precision": {"state": "numeric", "value": 0.5},
                        "context_recall": {"state": "numeric", "value": 0.75},
                        "faithfulness": {"state": "numeric", "value": 0.8},
                        "answer_relevancy": {"state": "numeric", "value": 0.6},
                        "answer_correctness": {"state": "numeric", "value": 0.7},
                    },
                    "ground_truth_sources": [{"relative_path": "retrieval/searcher.py"}],
                }
            ],
        }

        markdown = render_markdown_report(report)
        self.assertIn("CodeSeek RAGAS Validation Report", markdown)
        self.assertIn("`context_precision`", markdown)
        self.assertIn("`c1`", markdown)

    def test_family_baseline_snapshot_and_comparison(self) -> None:
        current_report = {
            "run_meta": {
                "dataset_name": "demo-current",
                "generated_at_utc": "2026-06-06T00:00:00Z",
                "case_count": 2,
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
                },
                {
                    "primary_intent": "SYMBOL",
                    "response_mode": "llm",
                    "ragas": {
                        "context_precision": {"state": "numeric", "value": 0.6},
                        "context_recall": {"state": "numeric", "value": 0.9},
                        "faithfulness": {"state": "numeric", "value": 0.7},
                        "answer_relevancy": {"state": "numeric", "value": 0.8},
                        "answer_correctness": {"state": "numeric", "value": 0.65},
                    },
                },
            ],
        }
        baseline = {
            "source_report": {
                "dataset_name": "demo-baseline",
                "generated_at_utc": "2026-06-05T00:00:00Z",
                "case_count": 2,
            },
            "families": {
                "primary_intent": {
                    "SYMBOL": {
                        "count": 2,
                        "metric_averages": {
                            "context_precision": 0.5,
                            "context_recall": 0.5,
                            "faithfulness": 0.5,
                            "answer_relevancy": 0.5,
                            "answer_correctness": 0.5,
                        },
                    }
                },
                "response_mode": {
                    "llm": {
                        "count": 2,
                        "metric_averages": {
                            "context_precision": 0.5,
                            "context_recall": 0.5,
                            "faithfulness": 0.5,
                            "answer_relevancy": 0.5,
                            "answer_correctness": 0.5,
                        },
                    }
                },
            },
        }

        snapshot = build_family_baseline_snapshot(current_report)
        comparison = compare_family_baselines(current_report, baseline)

        self.assertEqual(snapshot["source_report"]["dataset_name"], "demo-current")
        self.assertIn("SYMBOL", snapshot["families"]["primary_intent"])
        self.assertAlmostEqual(
            comparison["families"]["primary_intent"]["SYMBOL"]["metric_deltas"]["context_precision"],
            0.2,
        )
        self.assertAlmostEqual(
            comparison["families"]["response_mode"]["llm"]["metric_deltas"]["answer_correctness"],
            0.2,
        )


if __name__ == "__main__":
    unittest.main()
