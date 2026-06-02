"""Chunk generation stage."""

from pathlib import Path

from rag_ingestion.models.chunk import Chunk
from rag_ingestion.models.file import FileRecord
from rag_ingestion.models.parsed import ParsedFile


def generate_chunks(parsed: ParsedFile, file: FileRecord) -> list[Chunk]:
    """Convert parser output into source chunks."""
    lines = Path(file.path).read_text(encoding="utf-8", errors="ignore").splitlines(
        keepends=True
    )

    if parsed.parse_status == "failed":
        return [
            Chunk(
                file_path=file.path,
                relative_path=file.relative_path,
                language=file.language,
                chunk_type="file",
                start_line=1,
                end_line=len(lines),
                imports=parsed.imports,
                content="".join(lines),
            )
        ]

    chunks: list[Chunk] = []
    file_symbols = [symbol.symbol_name for symbol in parsed.symbols]
    if not parsed.symbols:
        return [
            Chunk(
                file_path=file.path,
                relative_path=file.relative_path,
                language=file.language,
                chunk_type="file",
                start_line=1,
                end_line=len(lines),
                imports=parsed.imports,
                file_symbols=file_symbols,
                content="".join(lines),
            )
        ]

    for symbol in parsed.symbols:
        content = "".join(lines[symbol.start_line - 1 : symbol.end_line])
        chunks.append(
            Chunk(
                file_path=file.path,
                relative_path=file.relative_path,
                language=file.language,
                chunk_type=symbol.symbol_type,
                symbol_name=symbol.symbol_name,
                parent_symbol=symbol.parent_symbol,
                signature=symbol.signature,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                imports=parsed.imports,
                calls=symbol.calls,
                parameters=symbol.parameters,
                methods=symbol.methods,
                docstring=symbol.docstring,
                content=content,
            )
        )

    return chunks
