"""Embedding generation stage."""

from __future__ import annotations

from rag_ingestion.config import (
    BATCH_SIZE,
    EMBEDDING_MODEL,
    EMBEDDING_INPUT_MAX_CODE_CHARS,
    EMBEDDING_INPUT_MAX_TOTAL_CHARS,
)
from rag_ingestion.models.chunk import Chunk
from rag_ingestion.utils.counters import PipelineCounters

_model = None

KNOWN_LABELS = {
    "File",
    "Language",
    "Type",
    "File Type",
    "Symbol",
    "Qualified Symbol",
    "Parent Symbol",
    "Signature",
    "Summary",
    "Description",
    "Purpose",
    "Facts",
    "Frameworks",
    "Dependencies",
    "Dev Dependencies",
    "Scripts",
    "Services",
    "Ports",
    "Environment Keys",
    "Feature Flags",
    "Provider Keys",
    "Entrypoints",
    "Config Tools",
    "Build System",
    "Base Image",
    "Workdir",
    "Package Manager",
    "Volumes",
    "Service Dependencies",
    "Setup Steps",
    "Usage Commands",
    "Architecture Notes",
    "Parameters",
    "Methods",
    "File Symbols",
    "Docstring",
}


def embed_chunks(
    chunks: list[Chunk], counters: PipelineCounters
) -> list[Chunk]:
    """Generate embeddings for chunks in batches."""
    model = _get_model()

    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        inputs = [_embedding_input(chunk) for chunk in batch]
        embeddings = model.encode(
            inputs,
            batch_size=BATCH_SIZE,
            show_progress_bar=True,
        )
        for chunk, embedding in zip(batch, embeddings, strict=True):
            chunk.embedding = embedding.tolist()
            counters.embeddings_generated += 1

    return chunks


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def _line(label: str, value: str | None) -> list[str]:
    if not value or not str(value).strip():
        return []
    return [f"{label}: {str(value).strip()}"]


def _list_line(label: str, values: list[str], limit: int = 20) -> list[str]:
    if not values:
        return []
    cleaned = [str(v).strip() for v in values if v and str(v).strip()]
    if not cleaned:
        return []
    return [f"{label}: {', '.join(cleaned[:limit])}"]


def _dict_line(label: str, values: dict, limit: int = 20) -> list[str]:
    if not values:
        return []
    parts = []
    for k, v in list(values.items())[:limit]:
        if not k or v is None:
            continue
        if isinstance(v, list):
            if not v:
                continue
            parts.append(f"{k} depends on {', '.join(str(item) for item in v if item)}")
        else:
            parts.append(f"{k}={v}")
    if not parts:
        return []
    return [f"{label}: {'; '.join(parts)}"]


def _embedding_input(chunk: Chunk) -> str:
    lines = []
    
    lines += _line("File", chunk.relative_path)
    lines += _line("Language", chunk.language)
    lines += _line("Type", chunk.chunk_type)
    lines += _line("File Type", chunk.file_type)
    lines += _line("Symbol", chunk.symbol_name)
    lines += _line("Qualified Symbol", chunk.qualified_symbol)
    lines += _line("Parent Symbol", chunk.parent_symbol)
    lines += _line("Signature", chunk.signature)
    lines += _line("Summary", chunk.summary)
    lines += _line("Description", chunk.description)
    lines += _line("Purpose", chunk.purpose)
    lines += _list_line("Facts", chunk.summary_facts)
    lines += _list_line("Frameworks", chunk.detected_frameworks)
    lines += _list_line("Dependencies", chunk.dependencies, limit=30)
    lines += _list_line("Dev Dependencies", chunk.dev_dependencies)
    lines += _dict_line("Scripts", chunk.scripts)
    lines += _list_line("Services", chunk.services)
    lines += _list_line("Ports", chunk.ports)
    lines += _list_line("Environment Keys", chunk.env_keys, limit=30)
    lines += _list_line("Feature Flags", chunk.feature_flags)
    lines += _list_line("Provider Keys", chunk.provider_keys)
    lines += _list_line("Entrypoints", chunk.entrypoints)
    lines += _list_line("Config Tools", chunk.config_tools)
    lines += _line("Build System", chunk.build_system)
    lines += _line("Base Image", chunk.base_image)
    lines += _line("Workdir", chunk.workdir)
    lines += _line("Package Manager", chunk.package_manager)
    lines += _list_line("Volumes", chunk.volumes)
    lines += _dict_line("Service Dependencies", chunk.service_dependencies)
    lines += _list_line("Setup Steps", chunk.setup_steps)
    lines += _list_line("Usage Commands", chunk.usage_commands)
    lines += _list_line("Architecture Notes", chunk.architecture_notes)
    lines += _list_line("Parameters", chunk.parameters)
    lines += _list_line("Methods", chunk.methods)
    lines += _list_line("File Symbols", chunk.file_symbols)
    lines += _line("Docstring", chunk.docstring)
    
    code = chunk.content or ""
    if code.strip():
        if len(code) > EMBEDDING_INPUT_MAX_CODE_CHARS:
            code = code[:EMBEDDING_INPUT_MAX_CODE_CHARS] + "... [truncated]"
        lines.append("Code:")
        lines.append(code)
        
    final_input = "\n".join(lines)
    if len(final_input) > EMBEDDING_INPUT_MAX_TOTAL_CHARS:
        final_input = final_input[:EMBEDDING_INPUT_MAX_TOTAL_CHARS] + "... [truncated]"
        
    return final_input
