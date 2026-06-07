import unittest
from unittest.mock import patch, MagicMock

from rag_ingestion.models.chunk import Chunk
from rag_ingestion.stages.description import (
    describe_chunks,
    _is_useful_chunk,
    _clean_description,
)
from rag_ingestion.stages.storage import _payload
from rag_ingestion.stages.embedder import _embedding_input


class ChunkDescriptionTests(unittest.TestCase):
    def test_is_useful_chunk(self) -> None:
        # 1. Useful types
        self.assertTrue(_is_useful_chunk(Chunk(content="def foo(): pass", chunk_type="function", relative_path="app.py")))
        self.assertTrue(_is_useful_chunk(Chunk(content="class Bar: pass", chunk_type="class", relative_path="app.py")))
        self.assertTrue(_is_useful_chunk(Chunk(content="def method(self): pass", chunk_type="method", relative_path="app.py")))
        self.assertTrue(_is_useful_chunk(Chunk(content="Some summary", chunk_type="repo_summary", relative_path="__repo_summary__.md")))

        # 2. Important files
        self.assertTrue(_is_useful_chunk(Chunk(content="project documentation info here", chunk_type="file", relative_path="README.md")))
        self.assertTrue(_is_useful_chunk(Chunk(content="package dependency config details", chunk_type="file", relative_path="package.json")))
        self.assertTrue(_is_useful_chunk(Chunk(content="some config variables", chunk_type="file", relative_path=".env.example")))

        # 3. Skip conditions
        self.assertFalse(_is_useful_chunk(Chunk(content="", chunk_type="function", relative_path="app.py")))
        self.assertFalse(_is_useful_chunk(Chunk(content="def x(): pass", chunk_type="function", relative_path=".gitignore")))
        self.assertFalse(_is_useful_chunk(Chunk(content="short", chunk_type="function", relative_path="app.py")))
        self.assertFalse(_is_useful_chunk(Chunk(content="unimportant configuration file content", chunk_type="file", relative_path="config.json")))

    def test_clean_description(self) -> None:
        raw = "**This** is a `clean` #description with *markdown*."
        cleaned = _clean_description(raw)
        self.assertEqual(cleaned, "This is a clean description with markdown.")

        # Length limit test (max 80 words)
        long_raw = " ".join(["word"] * 100)
        cleaned_long = _clean_description(long_raw)
        words = cleaned_long.split()
        self.assertEqual(len(words), 80)
        self.assertTrue(cleaned_long.endswith("..."))

    def test_describe_chunks_disabled_by_default(self) -> None:
        chunks = [
            Chunk(chunk_id="1", content="def foo(): pass", chunk_type="function", relative_path="app.py", summary="Func foo")
        ]
        # By default ENABLE_LLM_CHUNK_DESCRIPTIONS is False, so it should return chunks unchanged.
        with patch("rag_ingestion.stages.description.ENABLE_LLM_CHUNK_DESCRIPTIONS", False):
            result = describe_chunks(chunks)
            self.assertEqual(result[0].description, "")

    def test_describe_chunks_enabled_generates_descriptions(self) -> None:
        chunks = [
            Chunk(chunk_id="1", content="def foo(): pass", chunk_type="function", relative_path="app.py", summary="Func foo")
        ]
        
        provider_config = {"provider": "openai", "api_key": "test-key", "model": "gpt-4o-mini"}
        
        with patch("rag_ingestion.stages.description.ENABLE_LLM_CHUNK_DESCRIPTIONS", True), \
             patch("rag_ingestion.stages.description._resolve_active_llm_config", return_value=provider_config), \
             patch("retrieval.llm._chat_completion_request", return_value={
                 "choices": [{"message": {"content": "Generates a foo function."}}]
             }):
            
            result = describe_chunks(chunks)
            self.assertEqual(result[0].description, "Generates a foo function.")

    def test_describe_chunks_fallback_on_failure(self) -> None:
        chunks = [
            Chunk(chunk_id="1", content="def foo(): pass", chunk_type="function", relative_path="app.py", summary="Func foo")
        ]
        
        provider_config = {"provider": "openai", "api_key": "test-key", "model": "gpt-4o-mini"}
        
        with patch("rag_ingestion.stages.description.ENABLE_LLM_CHUNK_DESCRIPTIONS", True), \
             patch("rag_ingestion.stages.description._resolve_active_llm_config", return_value=provider_config), \
             patch("retrieval.llm._chat_completion_request", side_effect=RuntimeError("LLM offline")):
            
            result = describe_chunks(chunks)
            # Should fallback to summary
            self.assertEqual(result[0].description, "Func foo")

    def test_storage_payload_includes_description(self) -> None:
        chunk = Chunk(
            chunk_id="abc",
            relative_path="app.py",
            chunk_type="function",
            content="def foo(): pass",
            summary="Func foo",
            description="A beautiful function description.",
        )
        payload = _payload(chunk)
        self.assertEqual(payload["description"], "A beautiful function description.")

    def test_embedding_input_includes_description(self) -> None:
        chunk = Chunk(
            chunk_id="abc",
            relative_path="app.py",
            chunk_type="function",
            content="def foo(): pass",
            summary="Func foo",
            description="A beautiful function description.",
        )
        emb_input = _embedding_input(chunk)
        self.assertIn("Description: A beautiful function description.", emb_input)
