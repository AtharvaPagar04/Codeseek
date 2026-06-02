"""Embedding generation stage."""

from rag_ingestion.config import BATCH_SIZE, EMBEDDING_MODEL
from rag_ingestion.models.chunk import Chunk
from rag_ingestion.utils.counters import PipelineCounters

_model = None


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


def _embedding_input(chunk: Chunk) -> str:
    return "\n".join(
        [
            f"File: {chunk.relative_path}",
            f"Language: {chunk.language}",
            f"Type: {chunk.chunk_type}",
            f"Symbol: {chunk.symbol_name}",
            f"Summary: {chunk.summary}",
            f"Docstring: {chunk.docstring}",
            "Code:",
            chunk.content,
        ]
    )
