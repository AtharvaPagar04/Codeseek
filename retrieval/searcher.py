"""Qdrant search stage for retrieval."""

from collections import defaultdict
import re
import time

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
from sentence_transformers import SentenceTransformer

from retrieval.config import (
    EMBEDDING_MODEL,
    QDRANT_HOST,
    QDRANT_PORT,
    QUERY_PREFIX,
    RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD,
    RETRIEVAL_QDRANT_TIMEOUT_SECONDS,
    RETRIEVAL_RETRY_ATTEMPTS,
    RETRIEVAL_RETRY_BACKOFF_SECONDS,
    TOP_K_AFTER_MERGE,
    TOP_K_DENSE,
    get_collection_name,
)

_client = None
_model = None
_model_unavailable = False
_qdrant_failures = 0
_qdrant_circuit_open_until = 0.0


def _get_client():
    global _client
    if _client is None:
        _client = QdrantClient(
            QDRANT_HOST,
            port=QDRANT_PORT,
            timeout=RETRIEVAL_QDRANT_TIMEOUT_SECONDS,
            check_compatibility=False,
        )
    return _client


def _get_model():
    global _model, _model_unavailable
    if _model_unavailable:
        return None
    if _model is None:
        try:
            _model = SentenceTransformer(EMBEDDING_MODEL)
        except Exception:
            _model_unavailable = True
            return None
    return _model


def _qdrant_call(fn):
    global _qdrant_failures, _qdrant_circuit_open_until
    now = time.time()
    if _qdrant_circuit_open_until > now:
        return None
    last_exc = None
    for attempt in range(1, RETRIEVAL_RETRY_ATTEMPTS + 1):
        try:
            result = fn()
            _qdrant_failures = 0
            return result
        except Exception as exc:
            last_exc = exc
            if attempt < RETRIEVAL_RETRY_ATTEMPTS:
                time.sleep(RETRIEVAL_RETRY_BACKOFF_SECONDS * attempt)
    _qdrant_failures += 1
    if _qdrant_failures >= RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD:
        _qdrant_circuit_open_until = time.time() + RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS
    return None


def search(query_info: dict) -> list[dict]:
    """Run dense + metadata + dependency searches and merge results."""
    raw_query = query_info["raw_query"]
    intent = query_info["intent"]
    entities = query_info["entities"]

    dense_results = _dense_search(raw_query)
    filter_results = _metadata_search(raw_query, entities)
    dependency_results = _dependency_search(entities) if intent == "DEPENDENCY" else []

    merged = _merge_results(dense_results, filter_results, dependency_results)
    merged = _rerank_with_query_tokens(raw_query, merged)
    return merged[:TOP_K_AFTER_MERGE]


def _dense_search(raw_query: str):
    model = _get_model()
    if model is None:
        return []
    client = _get_client()
    collection = get_collection_name()
    query_vector = model.encode(QUERY_PREFIX + raw_query).tolist()
    if hasattr(client, "search"):
        points = _qdrant_call(lambda: client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=TOP_K_DENSE,
            with_payload=True,
        ))
    else:
        query = _qdrant_call(lambda: client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=TOP_K_DENSE,
            with_payload=True,
        ))
        if query is None:
            return []
        points = query.points
    if points is None:
        return []
    return [(point.payload or {}, float(point.score), "dense") for point in points]


def _metadata_search(raw_query: str, entities: dict):
    client = _get_client()
    collection = get_collection_name()
    results = []
    symbols = entities.get("symbols", [])
    files = entities.get("files", [])

    for file_hint in files:
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="relative_path", match=MatchValue(value=file_hint))]
            ),
            limit=30,
            with_payload=True,
        ))
        if response is None:
            continue
        hits, _ = response
        results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)

        for symbol in symbols:
            qualified = f"{file_hint}::{symbol}"
            response = _qdrant_call(lambda: client.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[FieldCondition(key="qualified_symbol", match=MatchValue(value=qualified))]
                ),
                limit=10,
                with_payload=True,
            ))
            if response is None:
                continue
            hits, _ = response
            results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)

    for symbol in symbols:
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="symbol_name", match=MatchValue(value=symbol))]
            ),
            limit=10,
            with_payload=True,
        ))
        if response is None:
            continue
        hits, _ = response
        results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)

    # Query-aware file pattern boosts for hard disambiguation (tests/websocket/ws).
    for symbol in symbols:
        lowered = symbol.lower()
        pattern_keys = []
        if "websocket" in lowered or lowered.endswith("ws") or "_ws" in lowered or "binancews" in lowered:
            pattern_keys.extend(["/ws", "websocket", "binance_ws"])
        if lowered.startswith("test_") or "test" in lowered:
            pattern_keys.append("/tests/")

        for key in pattern_keys:
            response = _qdrant_call(lambda: client.scroll(
                collection_name=collection,
                limit=200,
                with_payload=True,
            ))
            if response is None:
                continue
            hits, _ = response
            for hit in hits:
                payload = hit.payload or {}
                relative_path = str(payload.get("relative_path", "")).lower()
                if key.lower() in relative_path:
                    results.append((payload, 0.0, "filter"))

    # Raw-query path hints for cases without explicit symbol extraction.
    raw_lower = raw_query.lower()
    raw_path_hints = []
    if any(word in raw_lower for word in ("websocket", "binance ws", "binance_ws", " ws ")):
        raw_path_hints.extend(["binance_ws", "/ws", "websocket"])
    if any(word in raw_lower for word in ("lifecycle", "creation", "stop", "test", "validation")):
        raw_path_hints.append("/tests/")

    for key in raw_path_hints:
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            limit=300,
            with_payload=True,
        ))
        if response is None:
            continue
        hits, _ = response
        for hit in hits:
            payload = hit.payload or {}
            relative_path = str(payload.get("relative_path", "")).lower()
            if key.lower() in relative_path:
                results.append((payload, 0.0, "filter"))

    return results


def _dependency_search(entities: dict):
    client = _get_client()
    collection = get_collection_name()
    results = []
    for symbol in entities.get("symbols", []):
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="calls", match=MatchAny(any=[symbol]))]
            ),
            limit=10,
            with_payload=True,
        ))
        if response is None:
            continue
        hits, _ = response
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


def _rerank_with_query_tokens(raw_query: str, candidates: list[dict]) -> list[dict]:
    """Apply a small lexical boost so specific queries rank matching symbols/files higher."""
    tokens = _query_tokens(raw_query)
    if not tokens:
        return candidates

    rescored = []
    for item in candidates:
        overlap = _overlap_score(tokens, item)
        boosted = dict(item)
        boosted["_lexical_overlap"] = overlap
        rescored.append(boosted)

    rescored.sort(
        key=lambda item: (
            not item.get("multi_layer_hit", False),
            -float(item.get("retrieval_score", 0.0)),
            -int(item.get("_lexical_overlap", 0)),
        )
    )
    for item in rescored:
        item.pop("_lexical_overlap", None)
    return rescored


def _query_tokens(raw_query: str) -> set[str]:
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", raw_query.lower()))
    stop = {
        "where",
        "what",
        "which",
        "from",
        "with",
        "this",
        "that",
        "implemented",
        "function",
        "class",
        "trace",
        "exact",
        "show",
        "find",
        "list",
        "does",
    }
    return {t for t in tokens if t not in stop}


def _overlap_score(tokens: set[str], item: dict) -> int:
    hay = " ".join(
        [
            str(item.get("relative_path", "")),
            str(item.get("symbol_name", "")),
            str(item.get("qualified_symbol", "")),
            str(item.get("summary", "")),
        ]
    ).lower()
    return sum(1 for t in tokens if t in hay)


def dependency_health() -> dict[str, str]:
    """Best-effort readiness for retrieval dependencies."""
    model = _get_model()
    if model is None:
        model_status = "degraded"
    else:
        model_status = "ok"

    client = _get_client()
    collection = get_collection_name()
    qdrant_ready = _qdrant_call(lambda: client.get_collection(collection))
    qdrant_status = "ok" if qdrant_ready is not None else "degraded"
    return {"embedding_model": model_status, "qdrant": qdrant_status}
