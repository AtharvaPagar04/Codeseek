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

CONTENT_EXCERPT_CHARS = 12000


def store_chunks(
    chunks: list[Chunk],
    counters: PipelineCounters,
    collection_name: str | None = None,
    recreate_collection: bool | None = None,
) -> None:
    """Ensure the collection exists and upsert chunks by deterministic IDs."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    collection = collection_name or COLLECTION_NAME
    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT, check_compatibility=False)
    _ensure_collection(
        client=client,
        collection_name=collection,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        recreate_collection=recreate_collection,
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
        client.upsert(collection_name=collection, points=batch)
        counters.embeddings_stored += len(batch)


def delete_chunks_for_paths(
    relative_paths: list[str], collection_name: str | None = None
) -> None:
    """Delete points whose payload.relative_path belongs to removed or modified files."""
    if not relative_paths:
        return

    collection = collection_name or COLLECTION_NAME
    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT, check_compatibility=False)
    client.delete(
        collection_name=collection,
        points_selector=Filter(
            must=[
                FieldCondition(
                    key="relative_path",
                    match=MatchAny(any=relative_paths),
                )
            ]
        ),
    )


def _ensure_collection(
    client,
    vectors_config,
    collection_name: str,
    recreate_collection: bool | None = None,
) -> None:
    should_recreate = (
        RECREATE_COLLECTION_EACH_RUN
        if recreate_collection is None
        else recreate_collection
    )
    if should_recreate:
        client.recreate_collection(
            collection_name=collection_name,
            vectors_config=vectors_config,
        )
        return

    try:
        client.get_collection(collection_name)
    except Exception:
        client.create_collection(
            collection_name=collection_name,
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
        "qualified_symbol": chunk.qualified_symbol,
        "parent_symbol": chunk.parent_symbol,
        "signature": chunk.signature,
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
        "description": chunk.description,
        "file_type": chunk.file_type,
        "summary_facts": chunk.summary_facts,
        "detected_frameworks": chunk.detected_frameworks,
        "dependencies": chunk.dependencies,
        "dev_dependencies": chunk.dev_dependencies,
        "scripts": chunk.scripts,
        "services": chunk.services,
        "ports": chunk.ports,
        "env_keys": chunk.env_keys,
        "entrypoints": chunk.entrypoints,
        "config_tools": chunk.config_tools,
        "build_system": chunk.build_system,
        "volumes": chunk.volumes,
        "service_dependencies": chunk.service_dependencies,
        "base_image": chunk.base_image,
        "workdir": chunk.workdir,
        "package_manager": chunk.package_manager,
        "feature_flags": chunk.feature_flags,
        "provider_keys": chunk.provider_keys,
        "purpose": chunk.purpose,
        "setup_steps": chunk.setup_steps,
        "usage_commands": chunk.usage_commands,
        "architecture_notes": chunk.architecture_notes,
        "labels": getattr(chunk, "labels", []),
        "code_intent": getattr(chunk, "code_intent", ""),
        # label_confidences is intentionally excluded — ingestion-only, not persisted
        "content_excerpt": chunk.content[:CONTENT_EXCERPT_CHARS],
    }

