"""Chunk model passed through metadata, summary, embedding, and storage."""

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """A source-code chunk ready for enrichment and storage."""

    chunk_id: str = ""
    file_path: str = ""
    relative_path: str = ""
    language: str = ""
    chunk_type: str = ""
    symbol_name: str = ""
    qualified_symbol: str = ""
    parent_symbol: str = ""
    signature: str = ""
    start_line: int = 0
    end_line: int = 0
    chunk_part: int = 0
    total_parts: int = 0
    token_count: int = 0
    imports: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    file_symbols: list[str] = field(default_factory=list)
    docstring: str = ""
    summary: str = ""
    content: str = ""
    embedding: list[float] = field(default_factory=list)
