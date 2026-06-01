"""Expand retrieved chunks using metadata relationships."""

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from retrieval.config import (
    CALL_EXPANSION_LIMIT,
    COLLECTION_NAME,
    EXPAND_CALLS,
    EXPAND_PARENT,
    EXPAND_SPLIT_PARTS,
    QDRANT_HOST,
    QDRANT_PORT,
)

_client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)


def expand(candidates: list[dict], query_info: dict) -> list[dict]:
    """Attach related chunks (split parts, parent class, callees)."""
    del query_info
    seen: dict[str, dict] = {}

    for chunk in candidates:
        item = dict(chunk)
        item["expansion_type"] = "primary"
        seen[item["chunk_id"]] = item

    if EXPAND_SPLIT_PARTS:
        for chunk in candidates:
            if int(chunk.get("total_parts", 1)) > 1:
                _merge(seen, _split_parts(chunk), "split_part")

    if EXPAND_PARENT:
        for chunk in candidates:
            if chunk.get("chunk_type") == "method" and chunk.get("parent_symbol"):
                _merge(seen, _parent_chunk(chunk), "parent_class")

    if EXPAND_CALLS:
        call_targets = []
        for chunk in candidates:
            for call in chunk.get("calls", []):
                if call and call not in call_targets:
                    call_targets.append(call)
                if len(call_targets) >= CALL_EXPANSION_LIMIT:
                    break
            if len(call_targets) >= CALL_EXPANSION_LIMIT:
                break
        for target in call_targets:
            _merge(seen, _callee_chunks(target), "callee")

    return list(seen.values())


def _merge(seen: dict[str, dict], chunks: list[dict], expansion_type: str) -> None:
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id")
        if not chunk_id or chunk_id in seen:
            continue
        item = dict(chunk)
        item.setdefault("retrieval_score", 0.0)
        item["expansion_type"] = expansion_type
        seen[chunk_id] = item


def _split_parts(chunk: dict) -> list[dict]:
    hits, _ = _client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="relative_path", match=MatchValue(value=chunk["relative_path"])),
                FieldCondition(key="symbol_name", match=MatchValue(value=chunk.get("symbol_name", ""))),
            ]
        ),
        limit=50,
        with_payload=True,
    )
    payloads = [hit.payload or {} for hit in hits]
    payloads.sort(key=lambda item: int(item.get("chunk_part", 1)))
    return payloads


def _parent_chunk(chunk: dict) -> list[dict]:
    hits, _ = _client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="relative_path", match=MatchValue(value=chunk["relative_path"])),
                FieldCondition(key="symbol_name", match=MatchValue(value=chunk["parent_symbol"])),
                FieldCondition(key="chunk_type", match=MatchValue(value="class")),
            ]
        ),
        limit=1,
        with_payload=True,
    )
    return [hit.payload or {} for hit in hits]


def _callee_chunks(call_target: str) -> list[dict]:
    hits, _ = _client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="symbol_name", match=MatchValue(value=call_target))]
        ),
        limit=2,
        with_payload=True,
    )
    return [hit.payload or {} for hit in hits]
