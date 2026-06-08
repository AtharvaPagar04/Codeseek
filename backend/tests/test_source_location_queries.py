"""Tests for deterministic source-location queries."""

import os
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from retrieval.main import run_query
from retrieval.memory import ConversationMemory

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
