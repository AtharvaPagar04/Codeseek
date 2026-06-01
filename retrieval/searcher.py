"""Qdrant search stage for retrieval."""

from collections import defaultdict

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
from sentence_transformers import SentenceTransformer

from retrieval.config import (
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    QDRANT_HOST,
    QDRANT_PORT,
    QUERY_PREFIX,
    TOP_K_AFTER_MERGE,
    TOP_K_DENSE,
)

_client = QdrantClient(QDRANT_HOST, port=QDRANT_PORT)
_model = SentenceTransformer(EMBEDDING_MODEL)


def search(query_info: dict) -> list[dict]:
    """Run dense + metadata + dependency searches and merge results."""
    raw_query = query_info["raw_query"]
    intent = query_info["intent"]
    entities = query_info["entities"]

    dense_results = _dense_search(raw_query)
    filter_results = _metadata_search(entities)
    dependency_results = _dependency_search(entities) if intent == "DEPENDENCY" else []

    merged = _merge_results(dense_results, filter_results, dependency_results)
    return merged[:TOP_K_AFTER_MERGE]


def _dense_search(raw_query: str):
    query_vector = _model.encode(QUERY_PREFIX + raw_query).tolist()
    if hasattr(_client, "search"):
        points = _client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=TOP_K_DENSE,
            with_payload=True,
        )
    else:
        query = _client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=TOP_K_DENSE,
            with_payload=True,
        )
        points = query.points
    return [(point.payload or {}, float(point.score), "dense") for point in points]


def _metadata_search(entities: dict):
    results = []
    symbols = entities.get("symbols", [])
    files = entities.get("files", [])

    for file_hint in files:
        hits, _ = _client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="relative_path", match=MatchValue(value=file_hint))]
            ),
            limit=30,
            with_payload=True,
        )
        results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)

        for symbol in symbols:
            qualified = f"{file_hint}::{symbol}"
            hits, _ = _client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=Filter(
                    must=[FieldCondition(key="qualified_symbol", match=MatchValue(value=qualified))]
                ),
                limit=10,
                with_payload=True,
            )
            results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)

    for symbol in symbols:
        hits, _ = _client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="symbol_name", match=MatchValue(value=symbol))]
            ),
            limit=10,
            with_payload=True,
        )
        results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)

    return results


def _dependency_search(entities: dict):
    results = []
    for symbol in entities.get("symbols", []):
        hits, _ = _client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="calls", match=MatchAny(any=[symbol]))]
            ),
            limit=10,
            with_payload=True,
        )
        results.extend((hit.payload or {}, 0.0, "calls") for hit in hits)
    return results


def _merge_results(*layers):
    records = {}
    layer_hits = defaultdict(set)

    for layer in layers:
        for payload, score, source in layer:
            chunk_id = payload.get("chunk_id")
            if not chunk_id:
                continue
            if chunk_id not in records:
                records[chunk_id] = dict(payload)
                records[chunk_id]["retrieval_score"] = 0.0
            if source == "dense":
                records[chunk_id]["retrieval_score"] = max(records[chunk_id]["retrieval_score"], score)
            layer_hits[chunk_id].add(source)

    merged = []
    for chunk_id, payload in records.items():
        payload["multi_layer_hit"] = len(layer_hits[chunk_id]) > 1
        merged.append(payload)

    merged.sort(
        key=lambda item: (
            not item.get("multi_layer_hit", False),
            -float(item.get("retrieval_score", 0.0)),
        )
    )
    return merged
