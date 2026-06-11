"""Unit tests for Group 12 — LLM label refinement."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rag_ingestion.label_constants import LABEL_REGISTRY, MAX_TOTAL_LABELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk(
    *,
    labels=None,
    path="src/auth/session.py",
    chunk_type="function",
    file_type="",
    summary="Handle session token validation.",
    description="",
    content="token = db.get(session_id)",
    content_excerpt="",
    label_confidences=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        labels=list(labels or ["artifact:source-code", "question_use:code-snippet"]),
        relative_path=path,
        chunk_type=chunk_type,
        file_type=file_type,
        summary=summary,
        description=description,
        content=content,
        content_excerpt=content_excerpt,
        symbol_name="",
        qualified_symbol="",
        code_intent="",
        label_confidences=label_confidences or {},
    )


# ---------------------------------------------------------------------------
# 1–5: parse_llm_label_response
# ---------------------------------------------------------------------------

from rag_ingestion.stages.labeler import (
    merge_llm_labels,
    parse_llm_label_response,
    refine_chunk_labels_with_llm,
    refine_labels_with_llm,
    select_chunks_for_refinement,
)


def test_parse_valid_json_labels():
    """Valid JSON with registry labels is parsed correctly."""
    raw = json.dumps({"labels": ["domain:auth", "capability:session-validation"]})
    result = parse_llm_label_response(raw)
    assert "domain:auth" in result
    assert "capability:session-validation" in result


def test_parse_markdown_fenced_json():
    """JSON wrapped in markdown code fences is accepted."""
    raw = '```json\n{"labels": ["domain:auth"]}\n```'
    result = parse_llm_label_response(raw)
    assert "domain:auth" in result


def test_parse_invalid_json_returns_empty():
    """Malformed JSON returns empty list without raising."""
    assert parse_llm_label_response("not json at all") == []
    assert parse_llm_label_response("") == []
    assert parse_llm_label_response("{bad}") == []


def test_parse_filters_unknown_labels():
    """Labels not in LABEL_REGISTRY are silently dropped."""
    raw = json.dumps({"labels": ["domain:auth", "invented:label", "fake:thing"]})
    result = parse_llm_label_response(raw)
    assert "domain:auth" in result
    assert "invented:label" not in result
    assert "fake:thing" not in result


def test_parse_deduplicates_labels():
    """Duplicate labels appear only once."""
    raw = json.dumps({"labels": ["domain:auth", "domain:auth", "domain:auth"]})
    result = parse_llm_label_response(raw)
    assert result.count("domain:auth") == 1


# ---------------------------------------------------------------------------
# 6–10: merge_llm_labels
# ---------------------------------------------------------------------------

def test_merge_preserves_deterministic_labels():
    """Deterministic labels are never removed by the merge."""
    det = ["artifact:source-code", "question_use:code-snippet"]
    llm = ["domain:auth"]
    result = merge_llm_labels(det, llm)
    assert "artifact:source-code" in result
    assert "question_use:code-snippet" in result


def test_merge_adds_valid_llm_labels():
    """Valid LLM labels that aren't duplicates are appended."""
    det = ["artifact:source-code"]
    llm = ["domain:auth"]
    result = merge_llm_labels(det, llm)
    assert "domain:auth" in result


def test_merge_rejects_unknown_llm_labels():
    """Labels outside LABEL_REGISTRY are silently dropped."""
    det = ["artifact:source-code"]
    llm = ["invented:label"]
    result = merge_llm_labels(det, llm)
    assert "invented:label" not in result
    assert "artifact:source-code" in result


def test_merge_respects_max_total_labels():
    """Result never exceeds MAX_TOTAL_LABELS."""
    # Create a full deterministic set
    registry_labels = sorted(LABEL_REGISTRY.keys())
    det = registry_labels[:MAX_TOTAL_LABELS]
    llm = registry_labels[MAX_TOTAL_LABELS:]  # overflow
    result = merge_llm_labels(det, llm)
    assert len(result) <= MAX_TOTAL_LABELS


def test_merge_respects_category_caps():
    """Per-category caps are enforced when adding LLM labels."""
    from rag_ingestion.label_constants import MAX_LABELS_PER_CATEGORY

    # Fill domain to the cap with deterministic labels
    domain_labels = [l for l in LABEL_REGISTRY if l.startswith("domain:")]
    cap = MAX_LABELS_PER_CATEGORY.get("domain", 2)
    det = domain_labels[:cap]
    llm = domain_labels[cap:]  # one more domain label
    result = merge_llm_labels(det, llm)
    domain_in_result = [l for l in result if l.startswith("domain:")]
    assert len(domain_in_result) <= cap


# ---------------------------------------------------------------------------
# 11–12: select_chunks_for_refinement
# ---------------------------------------------------------------------------

def test_select_chunks_for_refinement_limits_count():
    """Returns at most max_chunks chunks."""
    chunks = [_chunk(labels=["artifact:source-code"], path=f"src/f{i}.py") for i in range(50)]
    selected = select_chunks_for_refinement(chunks, max_chunks=5)
    assert len(selected) <= 5


def test_select_chunks_prioritizes_weakly_labeled_chunks():
    """Chunks with fewer labels rank higher than well-labeled chunks."""
    weak = _chunk(labels=[], path="src/auth/validate.py", summary="Validate tokens.")
    strong = _chunk(
        labels=[
            "artifact:source-code", "domain:auth", "capability:session-validation",
            "question_use:code-snippet", "question_use:implementation",
        ],
        path="src/other.py",
        summary="Other stuff.",
    )
    selected = select_chunks_for_refinement([strong, weak], max_chunks=1)
    assert selected[0].relative_path == "src/auth/validate.py"


# ---------------------------------------------------------------------------
# 13: refine_labels_with_llm — failure returns empty
# ---------------------------------------------------------------------------

def test_refine_labels_with_llm_failure_returns_empty():
    """Connection error or any exception returns [] and does not raise."""
    import httpx

    chunk = _chunk()
    with patch("rag_ingestion.stages.labeler._httpx_mod") as mock_httpx:
        mock_httpx.post.side_effect = httpx.ConnectError("no server")
        result = refine_labels_with_llm(chunk, provider_config={"provider": "local"})
    assert result == []


# ---------------------------------------------------------------------------
# 14–15: refine_chunk_labels_with_llm
# ---------------------------------------------------------------------------

def test_refine_chunk_labels_disabled_returns_unchanged():
    """When ENABLE_LLM_LABEL_REFINEMENT is False, chunks are returned unchanged."""
    chunks = [_chunk()]
    original_labels = list(chunks[0].labels)

    with patch("rag_ingestion.stages.labeler.ENABLE_LLM_LABEL_REFINEMENT", False):
        result = refine_chunk_labels_with_llm(chunks, provider_config=None)

    assert result[0].labels == original_labels


def test_refine_chunk_labels_enabled_preserves_deterministic_labels():
    """When enabled, deterministic labels survive the merge even if LLM returns extras."""
    chunks = [_chunk()]
    original_labels = set(chunks[0].labels)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"labels": ["domain:auth"]})}}]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("rag_ingestion.stages.labeler.ENABLE_LLM_LABEL_REFINEMENT", True):
        with patch("rag_ingestion.stages.labeler._httpx_mod") as mock_httpx:
            mock_httpx.post.return_value = mock_response
            result = refine_chunk_labels_with_llm(
                chunks,
                provider_config={"provider": "local", "model": "test-model"},
            )

    result_labels = set(result[0].labels)
    # All original deterministic labels must still be present
    assert original_labels.issubset(result_labels), (
        f"Missing deterministic labels: {original_labels - result_labels}"
    )


# ---------------------------------------------------------------------------
# 16: label_confidences not stored in Qdrant payload
# ---------------------------------------------------------------------------

def test_refinement_does_not_store_label_confidences_in_payload():
    """label_confidences must not appear in the Qdrant point payload."""
    from rag_ingestion.stages import storage as storage_mod

    chunk = _chunk()
    chunk.label_confidences = {"domain:auth": 0.9}
    chunk.embedding = [0.0] * 384
    chunk.chunk_id = "test-chunk-id"
    chunk.content_excerpt = "token = db.get(session_id)"
    # Populate all fields that _payload reads so it doesn't AttributeError
    for field in (
        "imports", "calls", "dependencies", "dev_dependencies", "detected_frameworks",
        "services", "ports", "env_keys", "feature_flags", "provider_keys",
        "entrypoints", "config_tools", "volumes", "setup_steps", "usage_commands",
        "architecture_notes", "parameters", "methods", "file_symbols", "summary_facts",
    ):
        setattr(chunk, field, getattr(chunk, field, []))
    for field in ("scripts", "service_dependencies"):
        setattr(chunk, field, getattr(chunk, field, {}))
    for field in (
        "language", "purpose", "build_system", "base_image", "workdir",
        "package_manager", "docstring", "qualified_symbol", "parent_symbol",
        "signature", "repo_owner", "repo_name", "source_type",
        "file_path", "code_intent",
    ):
        setattr(chunk, field, getattr(chunk, field, ""))
    chunk.token_count = 10
    chunk.chunk_part = 1
    chunk.start_line = 1
    chunk.end_line = 5
    chunk.total_parts = 1
    chunk.content = "token = db.get(session_id)"

    # Call the private _payload function directly — no Qdrant connection needed
    payload = storage_mod._payload(chunk)
    assert "label_confidences" not in payload, (
        "label_confidences must NOT be stored in the Qdrant payload"
    )


def test_refine_chunk_labels_uses_configured_model():
    """Verify that refine_labels_with_llm uses CODESEEK_LABEL_MODEL."""
    chunk = _chunk()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"labels": ["domain:auth"]})}}]
    }
    
    with patch("rag_ingestion.stages.labeler._httpx_mod") as mock_httpx, \
         patch("rag_ingestion.config.CODESEEK_LABEL_MODEL", "qwen2.5-coder:3b"), \
         patch("rag_ingestion.config.CODESEEK_OLLAMA_KEEP_ALIVE", "30s"):
         
        mock_httpx.post.return_value = mock_response
        refine_labels_with_llm(chunk, provider_config={"provider": "local"})
        
        # Check that the model parameter in request body was 'qwen2.5-coder:3b'
        assert mock_httpx.post.call_count == 1
        call_args = mock_httpx.post.call_args
        assert call_args[1]["json"]["model"] == "qwen2.5-coder:3b"
        assert call_args[1]["json"]["keep_alive"] == "30s"


def test_refine_chunk_labels_batching_and_cleanup():
    """Verify that label refinement processes chunks in batches and runs cleanup."""
    chunks = [
        _chunk(path="src/f1.py"),
        _chunk(path="src/f2.py"),
        _chunk(path="src/f3.py"),
    ]
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"labels": []})}}]
    }
    
    with patch("rag_ingestion.stages.labeler.ENABLE_LLM_LABEL_REFINEMENT", True), \
         patch("rag_ingestion.stages.labeler._httpx_mod") as mock_httpx, \
         patch("rag_ingestion.config.CODESEEK_LABEL_REFINE_BATCH_SIZE", 2), \
         patch("rag_ingestion.config.CODESEEK_OLLAMA_STOP_MODEL_EVERY", 1), \
         patch("rag_ingestion.utils.gpu_cleanup.cleanup_after_batch") as mock_cleanup, \
         patch("rag_ingestion.utils.gpu_cleanup.ollama_stop_model") as mock_stop:
         
        mock_httpx.post.return_value = mock_response
        
        # Mock select_chunks_for_refinement to return all 3 chunks
        with patch("rag_ingestion.stages.labeler.select_chunks_for_refinement", return_value=chunks):
            refine_chunk_labels_with_llm(chunks, provider_config={"provider": "local"})
            
            # 3 chunks, batch size 2 -> 2 batches
            # cleanup_after_batch should be called twice (after each batch)
            assert mock_cleanup.call_count == 2
            
            # CODESEEK_OLLAMA_STOP_MODEL_EVERY = 1 -> ollama_stop_model should be called after each batch (twice)
            assert mock_stop.call_count == 2

