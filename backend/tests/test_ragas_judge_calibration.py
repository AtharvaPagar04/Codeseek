import unittest
import tempfile
import json
from pathlib import Path
import yaml
from unittest.mock import patch

from evals.ragas_judge_calibration import main

class TestRagasJudgeCalibration(unittest.TestCase):
    def test_expected_file_at_rank_1(self):
        # 1. expected file at rank 1: hit true, rank 1, reciprocal rank 1.0, precision calculated correctly
        queries_content = {
            "queries": [
                {
                    "id": "q001",
                    "query": "Where is the storage file?",
                    "category": "code_location",
                    "expected_files": ["backend/rag_ingestion/stages/storage.py"]
                }
            ]
        }
        
        trace_line = {
            "trace_id": "t001",
            "question": "Where is the storage file?",
            "answer": "Answer here",
            "retrieved_contexts": [
                {"relative_path": "backend/rag_ingestion/stages/storage.py"}
            ],
            "extra": {"query_id": "q001"}
        }
        
        ragas_report_content = {
            "status": "PASS",
            "metrics_run": ["context_precision"],
            "traces": [
                {
                    "trace_id": "t001",
                    "question": "Where is the storage file?",
                    "scores": {"context_precision": 1.0}
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            queries_file = tmp_path / "queries.yaml"
            trace_file = tmp_path / "traces.jsonl"
            ragas_report_file = tmp_path / "ragas_report.json"
            output_json_file = tmp_path / "output.json"
            output_md_file = tmp_path / "output.md"

            with open(queries_file, "w", encoding="utf-8") as f:
                yaml.dump(queries_content, f)

            with open(trace_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(trace_line) + "\n")

            with open(ragas_report_file, "w", encoding="utf-8") as f:
                json.dump(ragas_report_content, f)

            test_args = [
                "ragas_judge_calibration.py",
                "--queries", str(queries_file),
                "--trace-file", str(trace_file),
                "--ragas-report", str(ragas_report_file),
                "--output-json", str(output_json_file),
                "--output-md", str(output_md_file),
            ]

            with patch("sys.argv", test_args):
                main()

            with open(output_json_file, "r", encoding="utf-8") as f:
                output_json = json.load(f)

            row = output_json["per_query"][0]
            self.assertTrue(row["expected_context_file_hit"])
            self.assertEqual(row["expected_context_file_rank"], 1)
            self.assertEqual(row["expected_context_file_reciprocal_rank"], 1.0)
            self.assertAlmostEqual(row["expected_context_file_precision"], 1.0)
            self.assertEqual(row["expected_context_files_found"], ["backend/rag_ingestion/stages/storage.py"])
            self.assertEqual(row["expected_context_files_missing"], [])

    def test_expected_file_at_rank_3(self):
        # 2. expected file at rank 3: hit true, rank 3, reciprocal rank approx 0.3333, interpretation below rank 1
        queries_content = {
            "queries": [
                {
                    "id": "q002",
                    "query": "Query for rank 3?",
                    "category": "code_location",
                    "expected_files": ["backend/rag_ingestion/stages/storage.py"]
                }
            ]
        }
        
        trace_line = {
            "trace_id": "t002",
            "question": "Query for rank 3?",
            "answer": "Answer here",
            "retrieved_contexts": [
                {"relative_path": "backend/rag_ingestion/stages/parser.py"},
                {"relative_path": "backend/retrieval/query_intent.py"},
                {"relative_path": "backend/rag_ingestion/stages/storage.py"}
            ],
            "extra": {"query_id": "q002"}
        }
        
        ragas_report_content = {
            "status": "PASS",
            "metrics_run": ["context_precision"],
            "traces": [
                {
                    "trace_id": "t002",
                    "question": "Query for rank 3?",
                    "scores": {"context_precision": 0.5}
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            queries_file = tmp_path / "queries.yaml"
            trace_file = tmp_path / "traces.jsonl"
            ragas_report_file = tmp_path / "ragas_report.json"
            output_json_file = tmp_path / "output.json"
            output_md_file = tmp_path / "output.md"

            with open(queries_file, "w", encoding="utf-8") as f:
                yaml.dump(queries_content, f)

            with open(trace_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(trace_line) + "\n")

            with open(ragas_report_file, "w", encoding="utf-8") as f:
                json.dump(ragas_report_content, f)

            test_args = [
                "ragas_judge_calibration.py",
                "--queries", str(queries_file),
                "--trace-file", str(trace_file),
                "--ragas-report", str(ragas_report_file),
                "--output-json", str(output_json_file),
                "--output-md", str(output_md_file),
            ]

            with patch("sys.argv", test_args):
                main()

            with open(output_json_file, "r", encoding="utf-8") as f:
                output_json = json.load(f)

            row = output_json["per_query"][0]
            self.assertTrue(row["expected_context_file_hit"])
            self.assertEqual(row["expected_context_file_rank"], 3)
            self.assertAlmostEqual(row["expected_context_file_reciprocal_rank"], 0.3333333)
            self.assertAlmostEqual(row["expected_context_file_precision"], 1.0 / 3.0)
            self.assertIn("expected file retrieved below rank 1", row["interpretation"])

    def test_expected_file_missing(self):
        # 3. expected file missing: hit false, rank null, missing list includes it, retrieval_tuning_recommended true
        queries_content = {
            "queries": [
                {
                    "id": "q003",
                    "query": "Query for missing?",
                    "category": "code_location",
                    "expected_files": ["backend/rag_ingestion/stages/storage.py"]
                }
            ]
        }
        
        trace_line = {
            "trace_id": "t003",
            "question": "Query for missing?",
            "answer": "Answer here",
            "retrieved_contexts": [
                {"relative_path": "backend/rag_ingestion/stages/parser.py"}
            ],
            "extra": {"query_id": "q003"}
        }
        
        ragas_report_content = {
            "status": "PASS",
            "metrics_run": ["context_precision"],
            "traces": [
                {
                    "trace_id": "t003",
                    "scores": {"context_precision": 0.0}
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            queries_file = tmp_path / "queries.yaml"
            trace_file = tmp_path / "traces.jsonl"
            ragas_report_file = tmp_path / "ragas_report.json"
            output_json_file = tmp_path / "output.json"
            output_md_file = tmp_path / "output.md"

            with open(queries_file, "w", encoding="utf-8") as f:
                yaml.dump(queries_content, f)

            with open(trace_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(trace_line) + "\n")

            with open(ragas_report_file, "w", encoding="utf-8") as f:
                json.dump(ragas_report_content, f)

            test_args = [
                "ragas_judge_calibration.py",
                "--queries", str(queries_file),
                "--trace-file", str(trace_file),
                "--ragas-report", str(ragas_report_file),
                "--output-json", str(output_json_file),
                "--output-md", str(output_md_file),
            ]

            with patch("sys.argv", test_args):
                main()

            with open(output_json_file, "r", encoding="utf-8") as f:
                output_json = json.load(f)

            row = output_json["per_query"][0]
            self.assertFalse(row["expected_context_file_hit"])
            self.assertIsNone(row["expected_context_file_rank"])
            self.assertEqual(row["expected_context_files_missing"], ["backend/rag_ingestion/stages/storage.py"])
            self.assertTrue(output_json["retrieval_tuning_recommended"])
            self.assertIn("expected file missing from retrieved contexts", row["interpretation"])

    def test_empty_expected_files(self):
        # 4. empty expected_files: hit true, rank null, precision null, not recommend tuning
        queries_content = {
            "queries": [
                {
                    "id": "q004",
                    "query": "Query for empty expected?",
                    "category": "overview",
                    "expected_files": []
                }
            ]
        }
        
        trace_line = {
            "trace_id": "t004",
            "question": "Query for empty expected?",
            "answer": "Answer here",
            "retrieved_contexts": [
                {"relative_path": "backend/rag_ingestion/stages/parser.py"}
            ],
            "extra": {"query_id": "q004"}
        }
        
        ragas_report_content = {
            "status": "PASS",
            "metrics_run": ["context_precision"],
            "traces": [
                {
                    "trace_id": "t004",
                    "scores": {"context_precision": 0.0}
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            queries_file = tmp_path / "queries.yaml"
            trace_file = tmp_path / "traces.jsonl"
            ragas_report_file = tmp_path / "ragas_report.json"
            output_json_file = tmp_path / "output.json"
            output_md_file = tmp_path / "output.md"

            with open(queries_file, "w", encoding="utf-8") as f:
                yaml.dump(queries_content, f)

            with open(trace_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(trace_line) + "\n")

            with open(ragas_report_file, "w", encoding="utf-8") as f:
                json.dump(ragas_report_content, f)

            test_args = [
                "ragas_judge_calibration.py",
                "--queries", str(queries_file),
                "--trace-file", str(trace_file),
                "--ragas-report", str(ragas_report_file),
                "--output-json", str(output_json_file),
                "--output-md", str(output_md_file),
            ]

            with patch("sys.argv", test_args):
                main()

            with open(output_json_file, "r", encoding="utf-8") as f:
                output_json = json.load(f)

            row = output_json["per_query"][0]
            self.assertTrue(row["expected_context_file_hit"])
            self.assertIsNone(row["expected_context_file_rank"])
            self.assertIsNone(row["expected_context_file_precision"])
            self.assertFalse(output_json["retrieval_tuning_recommended"])

    def test_context_precision_mismatch_interpretation(self):
        # 5. context_precision mismatch: RAGAS CP = 0.0, expected hit true, interpretation includes disagreement
        queries_content = {
            "queries": [
                {
                    "id": "q005",
                    "query": "Query for mismatch?",
                    "category": "code_location",
                    "expected_files": ["backend/rag_ingestion/stages/storage.py"]
                }
            ]
        }
        
        trace_line = {
            "trace_id": "t005",
            "question": "Query for mismatch?",
            "answer": "Answer here",
            "retrieved_contexts": [
                {"relative_path": "backend/rag_ingestion/stages/storage.py"}
            ],
            "extra": {"query_id": "q005"}
        }
        
        ragas_report_content = {
            "status": "PASS",
            "metrics_run": ["context_precision"],
            "traces": [
                {
                    "trace_id": "t005",
                    "scores": {"context_precision": 0.0}
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            queries_file = tmp_path / "queries.yaml"
            trace_file = tmp_path / "traces.jsonl"
            ragas_report_file = tmp_path / "ragas_report.json"
            output_json_file = tmp_path / "output.json"
            output_md_file = tmp_path / "output.md"

            with open(queries_file, "w", encoding="utf-8") as f:
                yaml.dump(queries_content, f)

            with open(trace_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(trace_line) + "\n")

            with open(ragas_report_file, "w", encoding="utf-8") as f:
                json.dump(ragas_report_content, f)

            test_args = [
                "ragas_judge_calibration.py",
                "--queries", str(queries_file),
                "--trace-file", str(trace_file),
                "--ragas-report", str(ragas_report_file),
                "--output-json", str(output_json_file),
                "--output-md", str(output_md_file),
            ]

            with patch("sys.argv", test_args):
                main()

            with open(output_json_file, "r", encoding="utf-8") as f:
                output_json = json.load(f)

            row = output_json["per_query"][0]
            self.assertIn("RAGAS context_precision disagrees with deterministic expected-file hit", row["interpretation"])

    def test_aggregate_diagnostics(self):
        # 6. aggregate diagnostics: check hit count, rate, mean rank, mrr, precision
        queries_content = {
            "queries": [
                {
                    "id": "q1",
                    "query": "Q1",
                    "category": "code_location",
                    "expected_files": ["backend/a.py"]
                },
                {
                    "id": "q2",
                    "query": "Q2",
                    "category": "code_location",
                    "expected_files": ["backend/b.py"]
                },
                {
                    "id": "q3",
                    "query": "Q3",
                    "category": "code_location",
                    "expected_files": ["backend/e.py"]
                },
                {
                    "id": "q4",
                    "query": "Q4",
                    "category": "overview",
                    "expected_files": []
                }
            ]
        }
        
        traces = [
            {
                "trace_id": "t1",
                "question": "Q1",
                "answer": "Ans",
                "retrieved_contexts": [{"relative_path": "backend/a.py"}], # Hit at rank 1, precision 1.0, rr 1.0
                "extra": {"query_id": "q1"}
            },
            {
                "trace_id": "t2",
                "question": "Q2",
                "answer": "Ans",
                "retrieved_contexts": [
                    {"relative_path": "backend/c.py"},
                    {"relative_path": "backend/d.py"},
                    {"relative_path": "backend/b.py"} # Hit at rank 3, precision 0.3333, rr 0.3333
                ],
                "extra": {"query_id": "q2"}
            },
            {
                "trace_id": "t3",
                "question": "Q3",
                "answer": "Ans",
                "retrieved_contexts": [{"relative_path": "backend/f.py"}], # Miss: hit false, rank None, precision 0.0, rr None
                "extra": {"query_id": "q3"}
            },
            {
                "trace_id": "t4",
                "question": "Q4",
                "answer": "Ans",
                "retrieved_contexts": [{"relative_path": "backend/g.py"}], # Empty expected list: ignored
                "extra": {"query_id": "q4"}
            }
        ]
        
        ragas_report_content = {
            "status": "PASS",
            "metrics_run": ["context_precision"],
            "traces": [
                {"trace_id": "t1", "scores": {"context_precision": 1.0}},
                {"trace_id": "t2", "scores": {"context_precision": 0.5}},
                {"trace_id": "t3", "scores": {"context_precision": 0.0}},
                {"trace_id": "t4", "scores": {"context_precision": 0.0}}
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            queries_file = tmp_path / "queries.yaml"
            trace_file = tmp_path / "traces.jsonl"
            ragas_report_file = tmp_path / "ragas_report.json"
            output_json_file = tmp_path / "output.json"
            output_md_file = tmp_path / "output.md"

            with open(queries_file, "w", encoding="utf-8") as f:
                yaml.dump(queries_content, f)

            with open(trace_file, "w", encoding="utf-8") as f:
                for t in traces:
                    f.write(json.dumps(t) + "\n")

            with open(ragas_report_file, "w", encoding="utf-8") as f:
                json.dump(ragas_report_content, f)

            test_args = [
                "ragas_judge_calibration.py",
                "--queries", str(queries_file),
                "--trace-file", str(trace_file),
                "--ragas-report", str(ragas_report_file),
                "--output-json", str(output_json_file),
                "--output-md", str(output_md_file),
            ]

            with patch("sys.argv", test_args):
                main()

            with open(output_json_file, "r", encoding="utf-8") as f:
                output_json = json.load(f)

            aggr = output_json["deterministic_context_file_diagnostics"]
            self.assertEqual(aggr["queries_with_expected_files"], 3)
            self.assertEqual(aggr["expected_file_hit_count"], 2)
            self.assertAlmostEqual(aggr["expected_file_hit_rate"], 2.0 / 3.0)
            self.assertAlmostEqual(aggr["mean_expected_file_rank"], (1.0 + 3.0) / 2.0)
            self.assertAlmostEqual(aggr["mean_expected_file_reciprocal_rank"], (1.0 + (1.0 / 3.0)) / 2.0)
            self.assertAlmostEqual(aggr["mean_expected_context_file_precision"], (1.0 + (1.0 / 3.0) + 0.0) / 3.0)

if __name__ == "__main__":
    unittest.main()
