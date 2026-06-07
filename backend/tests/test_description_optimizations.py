"""Tests for description stage optimizations and smart chunk eligibility checking."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from rag_ingestion.config import (
    CHUNK_DESCRIPTION_MAX_OUTPUT_TOKENS,
    CHUNK_DESCRIPTION_SLEEP_SECONDS,
)
from rag_ingestion.models.chunk import Chunk
from rag_ingestion.stages.description import (
    _clean_description,
    _should_describe_chunk,
    describe_chunks,
)


def test_sleep_default_is_zero():
    assert CHUNK_DESCRIPTION_SLEEP_SECONDS == 0.0


def test_max_output_tokens_exists():
    assert CHUNK_DESCRIPTION_MAX_OUTPUT_TOKENS == 60


def test_should_describe_chunk_eligible():
    # README.md file chunk
    c1 = Chunk(relative_path="README.md", chunk_type="file", token_count=100, content="Readme content here")
    assert _should_describe_chunk(c1) is True

    # package.json file chunk
    c2 = Chunk(relative_path="package.json", chunk_type="file", token_count=100, content="package json content here")
    assert _should_describe_chunk(c2) is True

    # function chunk
    c3 = Chunk(relative_path="src/app.py", chunk_type="function", token_count=50, content="def foo(): pass\n" * 5)
    assert _should_describe_chunk(c3) is True

    # class chunk
    c4 = Chunk(relative_path="src/app.py", chunk_type="class", token_count=80, content="class Bar:\n    pass\n" * 5)
    assert _should_describe_chunk(c4) is True

    # repo_summary
    c5 = Chunk(relative_path="", chunk_type="repo_summary", token_count=20, content="Repo summary info here")
    assert _should_describe_chunk(c5) is True


def test_should_describe_chunk_skipped():
    # CSS file chunk
    c1 = Chunk(relative_path="style.css", chunk_type="file", token_count=100, content="body { color: red; }\n" * 5)
    assert _should_describe_chunk(c1) is False

    # .gitignore
    c2 = Chunk(relative_path=".gitignore", chunk_type="file", token_count=50, content="node_modules/\ndist/\n" * 5)
    assert _should_describe_chunk(c2) is False

    # overflow part 2
    c3 = Chunk(relative_path="src/app.py", chunk_type="function", token_count=100, chunk_part=2, content="def foo(): pass\n" * 5)
    assert _should_describe_chunk(c3) is False

    # tiny method below 120 tokens
    c4 = Chunk(relative_path="src/app.py", chunk_type="method", token_count=80, content="def foo(): pass\n" * 5)
    assert _should_describe_chunk(c4) is False

    # large method >= 120 tokens is described
    c5 = Chunk(relative_path="src/app.py", chunk_type="method", token_count=120, content="def foo(): pass\n" * 15)
    assert _should_describe_chunk(c5) is True

    # tiny chunk below 40 tokens is skipped
    c6 = Chunk(relative_path="src/app.py", chunk_type="function", token_count=30, content="def foo(): pass\n" * 5)
    assert _should_describe_chunk(c6) is False


def test_description_stage_emits_selection_and_timing_events():
    events = []

    def callback(stage, message, level="info", progress=None, total=None, metadata=None):
        events.append({
            "stage": stage,
            "message": message,
            "level": level,
            "metadata": metadata,
        })

    chunks = [
        Chunk(chunk_id="1", relative_path="README.md", chunk_type="file", token_count=100, content="Readme content here"),
        Chunk(chunk_id="2", relative_path="style.css", chunk_type="file", token_count=100, content="CSS content here"),
    ]
    provider = {"provider": "openai", "api_key": "test-key", "model": "gpt-4o-mini"}

    with patch("rag_ingestion.stages.description._generate_chunk_description", return_value="A nice description."):
        describe_chunks(chunks, enabled=True, provider_config=provider, event_callback=callback)

    # We should have a selection event first
    selection_evts = [e for e in events if "Selected" in e["message"]]
    assert len(selection_evts) == 1
    assert selection_evts[0]["stage"] == "description"
    # Should say: "Selected 1/2 chunks" since style.css is skipped
    assert "Selected 1/2" in selection_evts[0]["message"]

    # We should have a final timing event
    timing_evts = [e for e in events if "Completed LLM descriptions" in e["message"]]
    assert len(timing_evts) == 1
    assert timing_evts[0]["level"] == "success"
    assert timing_evts[0]["metadata"] is not None
    assert "elapsed_seconds" in timing_evts[0]["metadata"]


def test_description_stage_continues_on_failure():
    chunks = [
        Chunk(chunk_id="1", relative_path="README.md", chunk_type="file", token_count=100, content="Readme content here"),
        Chunk(chunk_id="2", relative_path="package.json", chunk_type="file", token_count=100, content="package json content here"),
    ]
    provider = {"provider": "openai", "api_key": "test-key", "model": "gpt-4o-mini"}

    calls = 0

    def flaky_generate(chunk, _cfg):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("LLM rate limit or timeout")
        return "Decent description."

    with patch("rag_ingestion.stages.description._generate_chunk_description", side_effect=flaky_generate):
        res = describe_chunks(chunks, enabled=True, provider_config=provider)

    assert len(res) == 2
    # Chunk 1 failed, should fall back to empty description or summary (which is empty here)
    assert res[0].description == ""
    # Chunk 2 succeeded
    assert res[1].description == "Decent description."


def test_description_text_cleaned_and_truncated():
    raw_text = "  Description:   This is a **bold** `code` chunk with multiple \n newlines.   "
    cleaned = _clean_description(raw_text)
    assert cleaned == "This is a bold code chunk with multiple newlines."

    # Test truncation
    long_text = "word " * 200
    cleaned_long = _clean_description(long_text)
    assert 390 <= len(cleaned_long) <= 410


def test_local_provider_calls_v1_chat_completions():
    # Verify that a local provider successfully calls /v1/chat/completions and returns the description.
    chunk = Chunk(chunk_id="1", relative_path="README.md", chunk_type="file", token_count=100, content="Readme content here")
    provider_config = {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen2.5-coder:3b-8k"}

    # Mock httpx.post to return a successful choice response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "OpenAI-compatible local description."
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_response) as mock_post:
        res = describe_chunks([chunk], enabled=True, provider_config=provider_config)
        
        # Check that it called /v1/chat/completions
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:11434/v1/chat/completions"
        assert kwargs["json"]["model"] == "qwen2.5-coder:3b-8k"
        
        # Verify description was populated
        assert res[0].description == "OpenAI-compatible local description."


def test_local_provider_falls_back_to_api_chat_on_404():
    # Verify that if /v1/chat/completions returns 404, it falls back to /api/chat.
    chunk = Chunk(chunk_id="1", relative_path="README.md", chunk_type="file", token_count=100, content="Readme content here")
    provider_config = {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen2.5-coder:3b-8k"}

    # Create responses: first call returns 404, second call returns 200 with native Ollama format
    response_404 = MagicMock()
    response_404.status_code = 404
    response_404.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="404 Not Found",
        request=MagicMock(),
        response=response_404
    )

    response_200 = MagicMock()
    response_200.status_code = 200
    response_200.json.return_value = {
        "message": {
            "content": "Native Ollama description."
        }
    }

    # Mock httpx.post to return 404 then 200
    with patch("httpx.post", side_effect=[response_404, response_200]) as mock_post:
        res = describe_chunks([chunk], enabled=True, provider_config=provider_config)
        
        # Should have called post twice
        assert mock_post.call_count == 2
        
        # First call was to /v1/chat/completions
        args1, kwargs1 = mock_post.call_args_list[0]
        assert args1[0] == "http://localhost:11434/v1/chat/completions"
        
        # Second call was to /api/chat
        args2, kwargs2 = mock_post.call_args_list[1]
        assert args2[0] == "http://localhost:11434/api/chat"
        assert kwargs2["json"]["model"] == "qwen2.5-coder:3b-8k"
        
        # Check description
        assert res[0].description == "Native Ollama description."


def test_local_provider_does_not_fallback_on_non_404_error():
    # Verify that if a non-404 error (e.g., 500) happens, it raises/propagates it without fallback.
    chunk = Chunk(chunk_id="1", relative_path="README.md", chunk_type="file", token_count=100, content="Readme content here")
    provider_config = {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen2.5-coder:3b-8k"}

    response_500 = MagicMock()
    response_500.status_code = 500
    response_500.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="500 Internal Server Error",
        request=MagicMock(),
        response=response_500
    )

    with patch("httpx.post", return_value=response_500) as mock_post:
        # Ingestion shouldn't crash — describe_chunks catches the error internally and sets description to summary or ""
        res = describe_chunks([chunk], enabled=True, provider_config=provider_config)
        
        # Should call only once
        assert mock_post.call_count == 1
        
        # No description was populated (falls back to summary or empty)
        assert res[0].description == ""


def test_remote_provider_still_uses_chat_completion_request():
    # Verify that remote provider still uses the existing remote _chat_completion_request path.
    chunk = Chunk(chunk_id="1", relative_path="README.md", chunk_type="file", token_count=100, content="Readme content here")
    provider_config = {"provider": "openai", "api_key": "test-key", "model": "gpt-4o-mini"}

    with patch("retrieval.llm._chat_completion_request") as mock_remote:
        mock_remote.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Remote provider description."
                    }
                }
            ]
        }
        
        res = describe_chunks([chunk], enabled=True, provider_config=provider_config)
        
        # Verify it called _chat_completion_request and did not call httpx.post directly
        mock_remote.assert_called_once()
        assert res[0].description == "Remote provider description."
