"""Qdrant storage stage."""

from rag_ingestion.config import COLLECTION_NAME, EMBEDDING_DIM, QDRANT_HOST, QDRANT_PORT
from rag_ingestion.models.chunk import Chunk
from rag_ingestion.utils.counters import PipelineCounters


def store_chunks(chunks: list[Chunk], counters: PipelineCounters) -> None:
    """Recreate the local Qdrant collection and upsert chunks."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
    client.recreate_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )

    points = [
        PointStruct(
            id=index,
            vector=chunk.embedding,
            payload=_payload(chunk),
        )
        for index, chunk in enumerate(chunks)
    ]

    for start in range(0, len(points), 128):
        batch = points[start : start + 128]
        client.upsert(collection_name=COLLECTION_NAME, points=batch)
        counters.embeddings_stored += len(batch)


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
