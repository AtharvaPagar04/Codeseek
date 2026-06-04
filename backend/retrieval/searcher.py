"""Qdrant search stage for retrieval."""

from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
import re
import time

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
from sentence_transformers import SentenceTransformer

from retrieval.config import (
    ENABLE_DENSE_RETRIEVAL,
    ENABLE_LEXICAL_RETRIEVAL,
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
    TOP_K_LEXICAL,
    get_collection_name,
    get_repo_root,
)

_client = None
_model = None
_model_unavailable = False
_qdrant_failures = 0
_qdrant_circuit_open_until = 0.0
_lexical_indexes: dict[str, "_LexicalIndex"] = {}


@dataclass
class _LexicalDocument:
    payload: dict
    tokens: list[str]


@dataclass
class _LexicalIndex:
    collection: str
    documents: list[_LexicalDocument]
    document_frequency: dict[str, int]
    average_length: float


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
    lexical_results = _lexical_search(raw_query) if ENABLE_LEXICAL_RETRIEVAL else []
    filter_results = _metadata_search(raw_query, entities)
    exact_entity_results = _exact_entity_search(entities)
    dependency_results = _dependency_search(entities) if intent == "DEPENDENCY" else []

    merged = _merge_results(dense_results, lexical_results, filter_results, exact_entity_results, dependency_results)
    if _is_overview_query(raw_query):
        merged = _inject_overview_candidates(merged)
    merged = _inject_import_backing_candidates(raw_query, merged)
    merged = _rerank_with_query_tokens(raw_query, merged)
    return merged[:TOP_K_AFTER_MERGE]


def _dense_search(raw_query: str):
    if not ENABLE_DENSE_RETRIEVAL:
        return []
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
                    results.append((payload, 0.0, "metadata"))

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
                results.append((payload, 0.0, "metadata"))

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


def _exact_entity_search(entities: dict) -> list[tuple[dict, float, str]]:
    terms = _entity_exact_terms(entities)
    if not terms:
        return []

    collection = get_collection_name()
    payloads = _scroll_collection_payloads(collection, max_points=1500)
    results: list[tuple[dict, float, str]] = []
    seen: set[str] = set()
    for payload in payloads:
        chunk_id = str(payload.get("chunk_id", "")).strip()
        if not chunk_id or chunk_id in seen:
            continue
        score = _exact_entity_score(payload, terms)
        if score <= 0:
            continue
        exact_payload = dict(payload)
        exact_payload["exact_entity_score"] = score
        results.append((exact_payload, score, "exact_entity"))
        seen.add(chunk_id)
    results.sort(key=lambda item: -item[1])
    return results[:TOP_K_AFTER_MERGE]


def _entity_exact_terms(entities: dict) -> list[str]:
    terms: list[str] = []
    for key in ("env_keys", "dependencies", "config_keys", "routes", "api_terms", "exact_terms"):
        value = entities.get(key) or []
        if isinstance(value, str):
            terms.append(value)
        elif isinstance(value, list):
            terms.extend(str(item) for item in value)
    cleaned = []
    for term in terms:
        value = term.strip()
        if len(value) < 3:
            continue
        cleaned.append(value)
    return sorted(set(cleaned), key=str.lower)


def _exact_entity_score(payload: dict, terms: list[str]) -> float:
    text = _exact_entity_text(payload)
    lowered_text = text.lower()
    structured_terms = _payload_structured_entity_terms(payload)
    score = 0.0
    for term in terms:
        lowered = term.lower()
        if term in structured_terms or lowered in structured_terms:
            score += 4.0
        elif term in text:
            score += 3.0
        elif lowered in lowered_text:
            score += 2.0
    return score


def _exact_entity_text(payload: dict) -> str:
    return " ".join(
        str(part)
        for part in (
            payload.get("relative_path", ""),
            payload.get("symbol_name", ""),
            payload.get("qualified_symbol", ""),
            payload.get("summary", ""),
            payload.get("content_excerpt", ""),
        )
        if part
    )


def _payload_structured_entity_terms(payload: dict) -> set[str]:
    terms: set[str] = set()
    for key in (
        "env_keys",
        "dependencies",
        "dev_dependencies",
        "detected_frameworks",
        "services",
        "ports",
        "entrypoints",
        "config_tools",
        "routes",
        "api_terms",
        "summary_facts",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                terms.add(str(item))
                terms.add(str(item).lower())
        elif isinstance(value, dict):
            for dict_key, dict_value in value.items():
                terms.add(str(dict_key))
                terms.add(str(dict_key).lower())
                if isinstance(dict_value, list):
                    for item in dict_value:
                        terms.add(str(item))
                        terms.add(str(item).lower())
                elif dict_value:
                    terms.add(str(dict_value))
                    terms.add(str(dict_value).lower())
        elif value:
            terms.add(str(value))
            terms.add(str(value).lower())
    return terms


def invalidate_lexical_index(collection_name: str | None = None) -> None:
    """Invalidate cached lexical indexes after ingestion updates a collection."""
    if collection_name:
        _lexical_indexes.pop(collection_name, None)
        return
    _lexical_indexes.clear()


def _lexical_search(raw_query: str) -> list[tuple[dict, float, str]]:
    query_tokens = _lexical_tokens(raw_query)
    if not query_tokens:
        return []
    collection = get_collection_name()
    index = _get_lexical_index(collection)
    if not index.documents:
        return []

    scored: list[tuple[dict, float, str]] = []
    for document in index.documents:
        score = _bm25_score(query_tokens, document.tokens, index)
        if score <= 0:
            continue
        scored.append((document.payload, score, "lexical"))
    scored.sort(key=lambda item: -item[1])
    return scored[:TOP_K_LEXICAL]


def _get_lexical_index(collection: str) -> _LexicalIndex:
    cached = _lexical_indexes.get(collection)
    if cached is not None:
        return cached

    payloads = _scroll_collection_payloads(collection)
    documents: list[_LexicalDocument] = []
    document_frequency: dict[str, int] = defaultdict(int)
    total_length = 0
    for payload in payloads:
        if not payload.get("chunk_id"):
            continue
        tokens = _lexical_tokens(_lexical_document_text(payload))
        if not tokens:
            continue
        documents.append(_LexicalDocument(payload=dict(payload), tokens=tokens))
        total_length += len(tokens)
        for token in set(tokens):
            document_frequency[token] += 1

    index = _LexicalIndex(
        collection=collection,
        documents=documents,
        document_frequency=dict(document_frequency),
        average_length=(total_length / len(documents)) if documents else 0.0,
    )
    _lexical_indexes[collection] = index
    return index


def _scroll_collection_payloads(collection: str, limit: int = 256, max_points: int = 5000) -> list[dict]:
    client = _get_client()
    payloads: list[dict] = []
    offset = None
    while len(payloads) < max_points:
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            limit=min(limit, max_points - len(payloads)),
            offset=offset,
            with_payload=True,
        ))
        if response is None:
            break
        hits, offset = response
        payloads.extend(hit.payload or {} for hit in hits)
        if not offset:
            break
    return payloads


def _lexical_document_text(payload: dict) -> str:
    parts = [
        payload.get("relative_path", ""),
        payload.get("symbol_name", ""),
        payload.get("qualified_symbol", ""),
        payload.get("chunk_type", ""),
        payload.get("language", ""),
        payload.get("signature", ""),
        payload.get("docstring", ""),
        payload.get("summary", ""),
        payload.get("content_excerpt", ""),
    ]
    for key in (
        "imports",
        "calls",
        "parameters",
        "methods",
        "file_symbols",
        "env_keys",
        "dependencies",
        "dev_dependencies",
        "detected_frameworks",
        "services",
        "entrypoints",
        "config_tools",
        "routes",
        "api_terms",
        "summary_facts",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value)
            parts.extend(str(item) for item in value.values())
        elif value:
            parts.append(str(value))
    return " ".join(str(part) for part in parts if part)


def _lexical_tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", text.lower())
    tokens: list[str] = []
    for token in raw:
        tokens.append(token)
        if "_" in token:
            tokens.extend(part for part in token.split("_") if len(part) > 1)
    return tokens


def _bm25_score(query_tokens: list[str], document_tokens: list[str], index: _LexicalIndex) -> float:
    if not document_tokens or not index.documents:
        return 0.0
    term_frequency: dict[str, int] = defaultdict(int)
    for token in document_tokens:
        term_frequency[token] += 1

    k1 = 1.5
    b = 0.75
    document_count = len(index.documents)
    doc_len = len(document_tokens)
    avg_len = index.average_length or 1.0
    score = 0.0
    for token in set(query_tokens):
        tf = term_frequency.get(token, 0)
        if tf <= 0:
            continue
        df = index.document_frequency.get(token, 0)
        idf = math.log(1 + (document_count - df + 0.5) / (df + 0.5))
        denom = tf + k1 * (1 - b + b * doc_len / avg_len)
        score += idf * (tf * (k1 + 1)) / denom
    return score


def _merge_results(*layers):
    records = {}
    layer_hits = defaultdict(set)

    for layer in layers:
        for rank, (payload, score, source) in enumerate(layer, start=1):
            chunk_id = payload.get("chunk_id")
            if not chunk_id:
                continue
            if chunk_id not in records:
                records[chunk_id] = dict(payload)
                records[chunk_id]["retrieval_score"] = 0.0
                records[chunk_id]["fusion_score"] = 0.0
                records[chunk_id]["exact_retrieval_hit"] = False
            if source == "dense":
                records[chunk_id]["retrieval_score"] = max(records[chunk_id]["retrieval_score"], score)
            if source in {"dense", "lexical", "metadata"}:
                records[chunk_id]["fusion_score"] += 1.0 / (60 + rank)
            if source in {"filter", "calls", "exact_entity"}:
                records[chunk_id]["exact_retrieval_hit"] = True
            layer_hits[chunk_id].add(source)

    merged = []
    for chunk_id, payload in records.items():
        payload["multi_layer_hit"] = len(layer_hits[chunk_id]) > 1
        merged.append(payload)

    merged.sort(
        key=lambda item: (
            not item.get("exact_retrieval_hit", False),
            not item.get("multi_layer_hit", False),
            -float(item.get("retrieval_score", 0.0)),
            -float(item.get("fusion_score", 0.0)),
        )
    )
    return merged


def _inject_import_backing_candidates(raw_query: str, candidates: list[dict]) -> list[dict]:
    tokens = _query_tokens(raw_query)
    if not tokens:
        return candidates

    backing_hits: list[dict] = []
    seen = {str(item.get("chunk_id", "")) for item in candidates if item.get("chunk_id")}
    for candidate in candidates[: min(len(candidates), 6)]:
        relative_path = str(candidate.get("relative_path", "")).strip()
        imports = list(candidate.get("imports") or [])
        if not relative_path or not imports:
            continue

        for statement in imports:
            for imported_name, module_path in _parse_named_imports(statement):
                if _identifier_token_overlap(imported_name, tokens) <= 0:
                    continue
                resolved = _resolve_import_relative_path(relative_path, module_path)
                if not resolved:
                    continue
                payloads = _fetch_import_symbol_chunks(resolved, imported_name)
                for payload in payloads:
                    chunk_id = str(payload.get("chunk_id", "")).strip()
                    if not chunk_id or chunk_id in seen:
                        continue
                    backing_hits.append(payload)
                    seen.add(chunk_id)
    return candidates + backing_hits


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
            not item.get("exact_retrieval_hit", False),
            not item.get("multi_layer_hit", False),
            -float(item.get("retrieval_score", 0.0)),
            -float(item.get("fusion_score", 0.0)),
            -int(item.get("_lexical_overlap", 0)),
        )
    )
    for item in rescored:
        item.pop("_lexical_overlap", None)
    return rescored


def _inject_overview_candidates(candidates: list[dict]) -> list[dict]:
    overview_hits = _repository_overview_candidates()
    if not overview_hits:
        return candidates

    seen = {str(item.get("chunk_id", "")) for item in candidates if item.get("chunk_id")}
    merged = list(candidates)
    for payload in overview_hits:
        chunk_id = str(payload.get("chunk_id", "")).strip()
        if not chunk_id or chunk_id in seen:
            continue
        merged.append(payload)
        seen.add(chunk_id)
    return merged


def _repository_overview_candidates() -> list[dict]:
    client = _get_client()
    collection = get_collection_name()
    response = _qdrant_call(lambda: client.scroll(
        collection_name=collection,
        limit=400,
        with_payload=True,
    ))
    if response is None:
        return []
    hits, _ = response
    payloads = [hit.payload or {} for hit in hits]
    payloads = [payload for payload in payloads if payload.get("chunk_id")]
    payloads.sort(
        key=lambda item: (
            -_overview_priority(item),
            item.get("relative_path", ""),
            int(item.get("start_line", 0)),
        )
    )

    chosen = []
    seen_files = set()
    for payload in payloads:
        score = _overview_priority(payload)
        if score <= 0:
            continue
        relative_path = str(payload.get("relative_path", "")).lower()
        if relative_path in seen_files and score < 16:
            continue
        chosen.append(payload)
        seen_files.add(relative_path)
        if len(chosen) >= max(6, TOP_K_AFTER_MERGE):
            break
    return chosen


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


def _identifier_token_overlap(identifier: str, tokens: set[str]) -> int:
    parts = set(re.findall(r"[a-zA-Z]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier).lower()))
    parts |= {part[:-1] for part in list(parts) if part.endswith("s") and len(part) > 3}
    score = 0
    lowered = identifier.lower()
    for token in tokens:
        singular = token[:-1] if token.endswith("s") and len(token) > 3 else token
        if token in parts or singular in parts:
            score += 2
        elif token in lowered or singular in lowered:
            score += 1
    return score


def _parse_named_imports(statement: str) -> list[tuple[str, str]]:
    match = re.search(r'import\s+\{([^}]+)\}\s+from\s+["\']([^"\']+)["\']', statement)
    if not match:
        return []
    names = []
    for part in match.group(1).split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        imported_name = cleaned.split(" as ", 1)[0].strip()
        if imported_name:
            names.append((imported_name, match.group(2).strip()))
    return names


def _resolve_import_relative_path(source_relative_path: str, module_path: str) -> str | None:
    repo_root = Path(get_repo_root())
    source_path = repo_root / source_relative_path

    if module_path.startswith("@/"):
        base = repo_root / "src" / module_path[2:]
    elif module_path.startswith("./") or module_path.startswith("../"):
        base = (source_path.parent / module_path).resolve()
    else:
        return None

    candidates = [
        base,
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.with_suffix(".js"),
        base.with_suffix(".jsx"),
        base / "index.ts",
        base / "index.tsx",
        base / "index.js",
        base / "index.jsx",
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return str(candidate.relative_to(repo_root))
            except ValueError:
                return None
    return None


def _fetch_import_symbol_chunks(relative_path: str, symbol_name: str) -> list[dict]:
    client = _get_client()
    collection = get_collection_name()
    response = _qdrant_call(lambda: client.scroll(
        collection_name=collection,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="relative_path", match=MatchValue(value=relative_path)),
                FieldCondition(key="symbol_name", match=MatchValue(value=symbol_name)),
            ]
        ),
        limit=10,
        with_payload=True,
    ))
    if response is None:
        return []
    hits, _ = response
    return [hit.payload or {} for hit in hits]


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


def _overview_priority(payload: dict) -> int:
    relative_path = str(payload.get("relative_path", "")).lower()
    symbol_name = str(payload.get("symbol_name", "")).lower()
    chunk_type = str(payload.get("chunk_type", "")).lower()
    file_type = str(payload.get("file_type", "")).lower()
    score = 0

    if not relative_path:
        return score
    if chunk_type == "repo_summary" or file_type == "repo_summary" or relative_path == "__repo_summary__.md":
        return 100
    if _is_test_path(relative_path) or symbol_name.startswith("test_"):
        return -10

    if relative_path in {"readme.md", "readme.mdx"}:
        score += 30
    if relative_path.endswith("package.json"):
        score += 24
    if relative_path.endswith(("package-lock.json", "pnpm-lock.yaml", "yarn.lock")):
        score += 18
    if relative_path.endswith("pyproject.toml") or relative_path.endswith("requirements.txt"):
        score += 22
    if relative_path.endswith((".env.example", ".env", "docker-compose.yml")):
        score += 20
    if relative_path.endswith(("vite.config.js", "vite.config.ts", "tailwind.config.js", "tailwind.config.ts")):
        score += 20
    if any(
        relative_path.endswith(name)
        for name in (
            "/src/app.jsx",
            "/src/app.tsx",
            "/src/main.jsx",
            "/src/main.tsx",
            "/src/main.py",
            "/app/page.tsx",
            "/main.py",
        )
    ):
        score += 20
    if any(key in relative_path for key in ("src/lib/data", "/data.ts", "/data.js")):
        score += 18
    if any(key in relative_path for key in ("project", "about", "skill", "contact", "education", "hero", "home")):
        score += 14
    if chunk_type == "file_summary":
        score += 8
    if payload.get("summary"):
        score += 4
    if symbol_name in {
        "app",
        "home",
        "portfolio",
        "projects",
        "about",
        "skills",
        "contact",
        "education",
        "personal",
        "skillcategories",
    }:
        score += 12
    if symbol_name in {"readme", "package_json", "packagejson"}:
        score += 10
    return score


def _is_overview_query(raw_query: str) -> bool:
    q = raw_query.lower()
    return any(
        phrase in q
        for phrase in (
            "what is this project about",
            "whats this project about",
            "project overview",
            "overview of the project",
            "what does this project do",
            "what does this app do",
            "tech stack",
            "architecture overview",
            "architecture",
            "stack used",
        )
    )


def _is_test_path(relative_path: str) -> bool:
    return "/test" in relative_path or relative_path.startswith("test")


def dependency_health() -> dict[str, str]:
    """Best-effort readiness for retrieval dependencies."""
    model = _get_model()
    if model is None:
        model_status = "degraded"
    else:
        model_status = "ok"

    client = _get_client()
    qdrant_ready = _qdrant_call(lambda: client.get_collections())
    qdrant_status = "ok" if qdrant_ready is not None else "degraded"
    return {"embedding_model": model_status, "qdrant": qdrant_status}
