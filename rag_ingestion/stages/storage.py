"""Qdrant storage stage."""

from rag_ingestion.config import (
    COLLECTION_NAME,
    EMBEDDING_DIM,
    QDRANT_HOST,
    QDRANT_PORT,
    RECREATE_COLLECTION_EACH_RUN,
)
from rag_ingestion.models.chunk import Chunk
from rag_ingestion.utils.counters import PipelineCounters


def store_chunks(chunks: list[Chunk], counters: PipelineCounters) -> None:
    """Ensure the collection exists and upsert chunks by deterministic IDs."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
    _ensure_collection(
        client=client,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )

    points = [
        PointStruct(
            id=_point_id(chunk),
            vector=chunk.embedding,
            payload=_payload(chunk),
        )
        for chunk in chunks
    ]

    for start in range(0, len(points), 128):
        batch = points[start : start + 128]
        client.upsert(collection_name=COLLECTION_NAME, points=batch)
        counters.embeddings_stored += len(batch)


def delete_chunks_for_paths(relative_paths: list[str]) -> None:
    """Delete points whose payload.relative_path belongs to removed files."""
    if not relative_paths:
        return

    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[
                FieldCondition(
                    key="relative_path",
                    match=MatchAny(any=relative_paths),
                )
            ]
        ),
    )


def _ensure_collection(client, vectors_config) -> None:
    if RECREATE_COLLECTION_EACH_RUN:
        client.recreate_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=vectors_config,
        )
        return

    try:
        client.get_collection(COLLECTION_NAME)
    except Exception:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=vectors_config,
        )


def _point_id(chunk: Chunk) -> str:
    if not chunk.chunk_id:
        raise ValueError("chunk_id is required before storage upsert")
    return chunk.chunk_id


def _payload(chunk: Chunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "file_path": chunk.file_path,
        "relative_path": chunk.relative_path,
        "language": chunk.language,
        "chunk_type": chunk.chunk_type,
        "symbol_name": chunk.symbol_name,
        "parent_symbol": chunk.parent_symbol,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "chunk_part": chunk.chunk_part,
        "total_parts": chunk.total_parts,
        "token_count": chunk.token_count,
        "imports": chunk.imports,
        "calls": chunk.calls,
        "parameters": chunk.parameters,
        "methods": chunk.methods,
        "file_symbols": chunk.file_symbols,
        "docstring": chunk.docstring,
        "summary": chunk.summary,
    }
