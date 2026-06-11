"""Tests for deterministic source-location queries."""

import os
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from retrieval.main import run_query
from retrieval.memory import ConversationMemory
from retrieval.source_filter import select_sources_for_display

class SourceLocationQueriesTests(unittest.TestCase):
    def test_qdrant_upsert_deterministic_answer(self) -> None:
        source = {
            "relative_path": "backend/rag_ingestion/stages/storage.py",
            "symbol_name": "upsert_chunks",
            "start_line": 10,
            "end_line": 20,
            "expansion_type": "primary",
            "labels": ["question_use:code-location"],
        }
        chunk = dict(source)
        chunk["chunk_id"] = "storage-1"
        chunk["retrieval_score"] = 0.9

        memory = ConversationMemory(max_turns=2)

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": str(repo_root),
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch(
                "retrieval.main.process_query",
                return_value={
                    "raw_query": "Show me where Qdrant upsert happens",
                    "intent": "SYMBOL",
                    "primary_intent": "SYMBOL",
                    "entities": {},
                },
            ), patch(
                "retrieval.main.search", return_value=[chunk]
            ), patch(
                "retrieval.main.expand", return_value=[chunk]
            ), patch(
                "retrieval.main.assemble", return_value=("context", [source], 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer"
            ) as generate_answer:
                answer, sources, token_count = run_query("Show me where Qdrant upsert happens", memory)

        self.assertIn("The Qdrant upsert happens in backend/rag_ingestion/stages/storage.py", answer)
        self.assertIn("client.upsert", answer)
        self.assertNotIn("Low confidence", answer)
        self.assertNotIn("Partial evidence", answer)
        generate_answer.assert_not_called()

    def test_fastapi_init_deterministic_answer(self) -> None:
        source = {
            "relative_path": "backend/retrieval/api_service.py",
            "symbol_name": "startup_checks",
            "start_line": 5,
            "end_line": 15,
            "expansion_type": "primary",
            "labels": ["question_use:code-location"],
        }
        chunk = dict(source)
        chunk["chunk_id"] = "api-1"
        chunk["retrieval_score"] = 0.9

        memory = ConversationMemory(max_turns=2)

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": str(repo_root),
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch(
                "retrieval.main.process_query",
                return_value={
                    "raw_query": "Where is the FastAPI app initialized?",
                    "intent": "SYMBOL",
                    "primary_intent": "SYMBOL",
                    "entities": {},
                },
            ), patch(
                "retrieval.main.search", return_value=[chunk]
            ), patch(
                "retrieval.main.expand", return_value=[chunk]
            ), patch(
                "retrieval.main.assemble", return_value=("context", [source], 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer"
            ) as generate_answer:
                answer, sources, token_count = run_query("Where is the FastAPI app initialized?", memory)

        self.assertIn("FastAPI app is initialized in backend/retrieval/api_service.py", answer)
        self.assertNotIn("Low confidence", answer)
        generate_answer.assert_not_called()

    def test_env_var_deterministic_answer(self) -> None:
        source = {
            "relative_path": "backend/retrieval/config.py",
            "symbol_name": "",
            "start_line": 1,
            "end_line": 50,
            "expansion_type": "primary",
            "labels": ["question_use:code-location"],
        }
        chunk = dict(source)
        chunk["chunk_id"] = "config-1"
        chunk["retrieval_score"] = 0.8

        memory = ConversationMemory(max_turns=2)

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            with patch.dict(
                os.environ,
                {
                    "RETRIEVAL_REPO_ROOT": str(repo_root),
                    "QDRANT_COLLECTION_NAME": "repository_chunks__local__tmprepo",
                    "CODESEEK_STRICT_ISOLATION": "0",
                },
                clear=False,
            ), patch(
                "retrieval.main.process_query",
                return_value={
                    "raw_query": "Where is environment variable handling implemented?",
                    "intent": "CONFIG",
                    "primary_intent": "CONFIG",
                    "entities": {},
                },
            ), patch(
                "retrieval.main.search", return_value=[chunk]
            ), patch(
                "retrieval.main.expand", return_value=[chunk]
            ), patch(
                "retrieval.main.assemble", return_value=("context", [source], 12)
            ), patch(
                "retrieval.main.select_sources_for_display", return_value=[source]
            ), patch(
                "retrieval.main.generate_answer"
            ) as generate_answer:
                answer, sources, token_count = run_query("Where is environment variable handling implemented?", memory)

        self.assertIn("Environment variable handling is implemented in backend/retrieval/config.py", answer)
        self.assertNotIn("Low confidence", answer)
        generate_answer.assert_not_called()

    def test_implementation_location_query_prefers_impl_over_docs(self) -> None:
        sources = [
            {
                "relative_path": "backend/docs/retrieval_docs/safe_eval_runner.md",
                "symbol_name": "safe_eval_runner_md",
                "start_line": 1,
                "end_line": 40,
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/evals/run_safe_evals.py",
                "symbol_name": "main",
                "start_line": 1,
                "end_line": 80,
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/evals/run_safe_evals.py",
                "symbol_name": "get_tail",
                "start_line": 81,
                "end_line": 110,
                "expansion_type": "primary",
            },
        ]

        selected = select_sources_for_display("Where is safe eval implemented?", sources)
        paths = [src["relative_path"] for src in selected]

        self.assertGreaterEqual(len(selected), 1)
        self.assertEqual("backend/evals/run_safe_evals.py", paths[0])
        self.assertNotIn("backend/docs/retrieval_docs/safe_eval_runner.md", paths[:1])
        self.assertNotIn("backend/docs/retrieval_docs/safe_eval_runner.md", paths)

    def test_explicit_docs_query_keeps_docs_primary(self) -> None:
        sources = [
            {
                "relative_path": "backend/docs/retrieval_docs/safe_eval_runner.md",
                "symbol_name": "safe_eval_runner_md",
                "start_line": 1,
                "end_line": 40,
                "expansion_type": "primary",
            },
            {
                "relative_path": "backend/evals/run_safe_evals.py",
                "symbol_name": "main",
                "start_line": 1,
                "end_line": 80,
                "expansion_type": "primary",
            },
        ]

        selected = select_sources_for_display("show me safe eval docs", sources)
        self.assertEqual("backend/docs/retrieval_docs/safe_eval_runner.md", selected[0]["relative_path"])

    def test_implementation_location_queries_prefer_code_over_docs_and_reports(self) -> None:
        def src(path: str, symbol: str, start: int = 1, end: int = 40) -> dict:
            return {
                "relative_path": path,
                "symbol_name": symbol,
                "start_line": start,
                "end_line": end,
                "expansion_type": "primary",
            }

        cases = [
            (
                "Where is safe eval implemented?",
                [
                    src("backend/docs/retrieval_docs/safe_eval_runner.md", "safe_eval_runner_md"),
                    src("backend/evals/run_safe_evals.py", "main"),
                    src("backend/evals/run_safe_evals.py", "get_tail"),
                ],
                "backend/evals/run_safe_evals.py",
            ),
            (
                "Where is evaluation report API implemented?",
                [
                    src("backend/docs/retrieval_docs/eval_report_api.md", "eval_report_api_md"),
                    src("backend/retrieval/api_service.py", "get_latest_evaluation_report_v1"),
                    src("backend/retrieval/eval_reports.py", "get_latest_evaluation_report"),
                ],
                "backend/retrieval/api_service.py",
            ),
            (
                "Where is repo freshness implemented?",
                [
                    src("backend/reports/repo_freshness_report.md", "repo_freshness_report"),
                    src("backend/retrieval/session_indexer.py", "compute_repo_freshness_status"),
                ],
                "backend/retrieval/session_indexer.py",
            ),
            (
                "Where is description cooldown implemented?",
                [
                    src("backend/docs/retrieval_docs/description_cooldown.md", "description_cooldown_md"),
                    src("backend/rag_ingestion/stages/description.py", "run_description_stage"),
                ],
                "backend/rag_ingestion/stages/description.py",
            ),
            (
                "Where is embedding cooldown implemented?",
                [
                    src("backend/docs/retrieval_docs/embedding_cooldown.md", "embedding_cooldown_md"),
                    src("backend/rag_ingestion/stages/embedder.py", "run_embedder_stage"),
                ],
                "backend/rag_ingestion/stages/embedder.py",
            ),
        ]

        for query, sources, expected_primary in cases:
            with self.subTest(query=query):
                selected = select_sources_for_display(query, sources)
                self.assertGreaterEqual(len(selected), 1)
                self.assertEqual(expected_primary, selected[0]["relative_path"])

    def test_format_source_location_target_shape_reordering(self) -> None:
        from retrieval.code_answers import _format_source_location_target_shape
        sources = [
            {"relative_path": "backend/retrieval/code_answers.py", "symbol_name": "build_flow_answer"},
            {"relative_path": "backend/rag_ingestion/stages/storage.py", "symbol_name": "upsert_chunks"},
        ]
        # Test 1: Avoid code_answers.py as top source if another file exists
        result1 = _format_source_location_target_shape(list(sources))
        self.assertIn("The implementation is in:\n\n* `backend/rag_ingestion/stages/storage.py`\n  * symbol/function: `upsert_chunks`", result1)

        # Test 2: Prioritize file mentioned in why_override
        sources2 = [
            {"relative_path": "backend/retrieval/api_service.py", "symbol_name": "get_session"},
            {"relative_path": "backend/retrieval/session_indexer.py", "symbol_name": "create_session"},
        ]
        why_override = "The session creation happens in backend/retrieval/session_indexer.py inside create_session."
        result2 = _format_source_location_target_shape(list(sources2), why_override=why_override)
        self.assertIn("* `backend/retrieval/session_indexer.py`\n  * symbol/function: `create_session`", result2)
