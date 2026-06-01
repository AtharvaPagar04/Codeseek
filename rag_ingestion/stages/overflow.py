"""Overflow handling stage."""

from dataclasses import replace

from rag_ingestion.config import MAX_CHUNK_TOKENS, SLIDING_OVERLAP, SLIDING_WINDOW_SIZE
from rag_ingestion.models.chunk import Chunk


def handle_overflow(chunks: list[Chunk]) -> list[Chunk]:
    """Split oversized chunks using a sliding line window."""
    expanded: list[Chunk] = []

    for chunk in chunks:
        token_count = _count_tokens(chunk.content)
        if token_count <= MAX_CHUNK_TOKENS:
            chunk.token_count = token_count
            chunk.chunk_part = 1
            chunk.total_parts = 1
            expanded.append(chunk)
            continue

        windows = _line_windows(chunk.content)
        total_parts = len(windows)
        for index, content in enumerate(windows, start=1):
            expanded.append(
                replace(
                    chunk,
                    content=content,
                    chunk_part=index,
                    total_parts=total_parts,
                    token_count=_count_tokens(content),
                )
            )

    return expanded


def _count_tokens(content: str) -> int:
    import tiktoken

    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(content))


def _line_windows(content: str) -> list[str]:
    lines = content.splitlines(keepends=True)
    if not lines:
        return [""]

    step = max(1, SLIDING_WINDOW_SIZE - SLIDING_OVERLAP)
    windows: list[str] = []
    for start in range(0, len(lines), step):
        window = lines[start : start + SLIDING_WINDOW_SIZE]
        if not window:
            break
        windows.append("".join(window))
        if start + SLIDING_WINDOW_SIZE >= len(lines):
            break
    return windows
