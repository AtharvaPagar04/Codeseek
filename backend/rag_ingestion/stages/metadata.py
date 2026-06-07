"""Metadata generation stage."""

import hashlib

from rag_ingestion.models.chunk import Chunk


def build_metadata(chunk: Chunk) -> Chunk:
    """Populate deterministic chunk ID and token count."""
    if chunk.chunk_type in {"file", "repo_summary"}:
        raw = f"{chunk.relative_path}::__file__::{chunk.chunk_part}"
    else:
        raw = (
            f"{chunk.relative_path}::{chunk.parent_symbol}::"
            f"{chunk.symbol_name}::{chunk.chunk_part}"
        )

    chunk.chunk_id = hashlib.sha256(raw.encode()).hexdigest()[:32]
    chunk.qualified_symbol = _qualified_symbol(chunk)
    chunk.token_count = _count_tokens(chunk.content)
    return chunk


def _count_tokens(content: str) -> int:
    import tiktoken

    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:  # pragma: no cover - offline fallback for test environments
        class _FallbackEncoding:
            def encode(self, text: str) -> list[int]:
                return list(text.encode("utf-8"))

            def decode(self, tokens: list[int]) -> str:
                return bytes(tokens).decode("utf-8", errors="ignore")

        encoding = _FallbackEncoding()
    return len(encoding.encode(content))


def _qualified_symbol(chunk: Chunk) -> str:
    if chunk.chunk_type in {"file", "repo_summary"}:
        return f"{chunk.relative_path}::__file__"
    if chunk.chunk_type == "method" and chunk.parent_symbol:
        return f"{chunk.relative_path}::{chunk.parent_symbol}.{chunk.symbol_name}"
    return f"{chunk.relative_path}::{chunk.symbol_name}"
