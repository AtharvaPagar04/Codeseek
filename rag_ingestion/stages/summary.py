"""Summary generation stage."""

from rag_ingestion.models.chunk import Chunk


def generate_summary(chunk: Chunk) -> str:
    """Generate a deterministic AST-based chunk summary."""
    if chunk.chunk_type == "function":
        lines = [f"Function: {chunk.symbol_name}"]
        if chunk.parameters:
            lines.append(f"Parameters: {', '.join(chunk.parameters)}")
        if chunk.docstring:
            lines.append(f"Docstring: {chunk.docstring}")
        return "\n".join(lines)

    if chunk.chunk_type == "method":
        lines = [f"Method: {chunk.symbol_name}", f"Class: {chunk.parent_symbol}"]
        if chunk.parameters:
            lines.append(f"Parameters: {', '.join(chunk.parameters)}")
        if chunk.docstring:
            lines.append(f"Docstring: {chunk.docstring}")
        return "\n".join(lines)

    if chunk.chunk_type == "class":
        lines = [f"Class: {chunk.symbol_name}"]
        if chunk.methods:
            lines.append(f"Methods: {', '.join(chunk.methods)}")
        if chunk.docstring:
            lines.append(f"Docstring: {chunk.docstring}")
        return "\n".join(lines)

    if chunk.chunk_type == "file":
        lines = [f"File: {chunk.relative_path}"]
        if chunk.file_symbols:
            lines.append(f"Symbols: {', '.join(chunk.file_symbols)}")
        return "\n".join(lines)

    return ""
