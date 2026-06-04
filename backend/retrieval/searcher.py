"""Qdrant search stage for retrieval."""

from collections import defaultdict
from dataclasses import dataclass
import json
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
IMPORT_TRACE_DEPTH_LIMIT = 3
TRACE_EXPANDED_CHUNKS_LIMIT = 6


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
    primary_intent = query_info.get("primary_intent", intent)
    entities = query_info["entities"]

    dense_results = _dense_search(raw_query)
    lexical_results = _lexical_search(raw_query) if ENABLE_LEXICAL_RETRIEVAL else []
    filter_results = _metadata_search(raw_query, entities)
    exact_entity_results = _exact_entity_search(entities)
    dependency_results = _dependency_search(entities) if intent == "DEPENDENCY" else []

    merged = _merge_results(dense_results, lexical_results, filter_results, exact_entity_results, dependency_results)
    # Inject repo-summary and structured overview evidence for any query whose
    # primary intent is broad/structural.  The phrase-based gate is kept as a
    # fast-path fallback for cases where intent scoring disagrees.
    if _is_overview_intent(primary_intent) or _is_overview_query(raw_query):
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
        found_file_hint = False
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="relative_path", match=MatchValue(value=file_hint))]
            ),
            limit=30,
            with_payload=True,
        ))
        if response is not None:
            hits, _ = response
            found_file_hint = bool(hits)
            results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)
        if not found_file_hint:
            local_payload = _local_file_hint_payload(file_hint)
            if local_payload:
                results.append((local_payload, 0.0, "filter"))

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
        found_symbol = False
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="symbol_name", match=MatchValue(value=symbol))]
            ),
            limit=10,
            with_payload=True,
        ))
        if response is None:
            hits = []
        else:
            hits, _ = response
            found_symbol = bool(hits)
            results.extend((hit.payload or {}, 0.0, "filter") for hit in hits)
        if not found_symbol:
            local_payload = _local_symbol_hint_payload(symbol)
            if local_payload:
                results.append((local_payload, 0.0, "filter"))

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


def _local_file_hint_payload(file_hint: str) -> dict | None:
    """Return grounded local-file evidence when Qdrant lacks an exact file hit."""
    clean_hint = str(file_hint).strip().lstrip("/")
    if not clean_hint or ".." in Path(clean_hint).parts:
        return None
    repo_root = Path(get_repo_root()).resolve()
    resolved = _resolve_local_file_hint(repo_root, clean_hint)
    if not resolved:
        return None
    relative_path, path = resolved
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    summary = f"File: {relative_path}"
    return {
        "chunk_id": f"local-file::{relative_path}",
        "relative_path": relative_path,
        "symbol_name": path.name,
        "qualified_symbol": f"{relative_path}::<file>",
        "chunk_type": "file_summary",
        "file_type": path.name.lower(),
        "language": "text",
        "start_line": 1,
        "end_line": max(1, len(lines)),
        "summary": summary,
        "content": text,
        "content_excerpt": text[:4000],
    }


def _resolve_local_file_hint(repo_root: Path, clean_hint: str) -> tuple[str, Path] | None:
    exact_path = (repo_root / clean_hint).resolve()
    try:
        exact_path.relative_to(repo_root)
    except ValueError:
        return None
    if exact_path.is_file():
        return clean_hint, exact_path

    matches: list[tuple[int, str, Path]] = []
    hint_name = Path(clean_hint).name
    for candidate in repo_root.rglob(hint_name):
        if not candidate.is_file():
            continue
        try:
            relative = candidate.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if relative == clean_hint or relative.endswith(f"/{clean_hint}") or candidate.name == hint_name:
            matches.append((_local_file_hint_priority(relative), relative, candidate.resolve()))
    if not matches:
        return None
    _priority, relative, path = sorted(matches, key=lambda item: (item[0], item[1]))[0]
    return relative, path


def _local_file_hint_priority(relative_path: str) -> int:
    lower = relative_path.lower()
    if lower.startswith("backend/"):
        return 0
    if lower.startswith("deploy/"):
        return 1
    if lower.startswith("frontend/"):
        return 2
    return 3


def _local_symbol_hint_payload(symbol: str) -> dict | None:
    clean_symbol = str(symbol).strip()
    if not clean_symbol or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean_symbol):
        return None
    repo_root = Path(get_repo_root()).resolve()
    for path in _iter_local_symbol_files(repo_root):
        payload = _local_symbol_payload_from_file(repo_root, path, clean_symbol)
        if payload:
            return payload
    return None


def _iter_local_symbol_files(repo_root: Path):
    skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"}
    for path in repo_root.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        yield path


def _local_symbol_payload_from_file(repo_root: Path, path: Path, symbol: str) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        relative_path = path.resolve().relative_to(repo_root).as_posix()
    except (OSError, ValueError):
        return None
    pattern = re.compile(rf"^(async\s+def|def|class)\s+{re.escape(symbol)}\b")
    start_index = None
    for index, line in enumerate(lines):
        if pattern.match(line):
            start_index = index
            break
    if start_index is None:
        return None
    end_index = len(lines) - 1
    next_symbol_pattern = re.compile(r"^(async\s+def|def|class)\s+[A-Za-z_][A-Za-z0-9_]*\b")
    for index in range(start_index + 1, len(lines)):
        if next_symbol_pattern.match(lines[index]):
            end_index = index - 1
            break
    content = "\n".join(lines[start_index : end_index + 1])
    start_line = start_index + 1
    end_line = end_index + 1
    return {
        "chunk_id": f"local-symbol::{relative_path}::{symbol}",
        "relative_path": relative_path,
        "symbol_name": symbol,
        "qualified_symbol": f"{relative_path}::{symbol}",
        "chunk_type": "function",
        "language": "python",
        "start_line": start_line,
        "end_line": end_line,
        "summary": f"Function: {symbol}",
        "content": content,
        "content_excerpt": content[:4000],
    }


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
        for hit in hits:
            payload = dict(hit.payload or {})
            payload["expansion_type"] = "callee"
            payload["support_kind"] = "dependency_edge"
            results.append((payload, 0.0, "calls"))
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
    visited_edges: set[tuple[str, str, str]] = set()
    for candidate in candidates[: min(len(candidates), 6)]:
        if len(backing_hits) >= TRACE_EXPANDED_CHUNKS_LIMIT:
            break
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
                edge = (relative_path, resolved, imported_name)
                if edge in visited_edges:
                    continue
                visited_edges.add(edge)
                payloads = _fetch_import_symbol_chunks(resolved, imported_name)
                for payload in payloads:
                    if len(backing_hits) >= TRACE_EXPANDED_CHUNKS_LIMIT:
                        break
                    chunk_id = str(payload.get("chunk_id", "")).strip()
                    if not chunk_id or chunk_id in seen:
                        continue
                    backing_payload = dict(payload)
                    backing_payload["expansion_type"] = "supporting_import"
                    backing_payload["support_kind"] = "import_backing"
                    backing_payload["supporting_from"] = relative_path
                    backing_payload["supporting_import_name"] = imported_name
                    backing_hits.append(backing_payload)
                    seen.add(chunk_id)
                if len(backing_hits) >= TRACE_EXPANDED_CHUNKS_LIMIT:
                    break
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
    """Merge repo-summary and structured overview chunks into the candidate list.

    High-priority overview chunks (repo_summary first, then README/manifests)
    are PREPENDED so they survive the TOP_K_AFTER_MERGE cutoff at the end of
    search(). Each injected chunk is assigned a baseline retrieval_score so
    _rerank_with_query_tokens does not push them below scored dense hits.
    """
    overview_hits = _repository_overview_candidates()
    if not overview_hits:
        return candidates

    existing_ids = {str(item.get("chunk_id", "")) for item in candidates if item.get("chunk_id")}

    to_prepend: list[dict] = []
    for payload in overview_hits:
        chunk_id = str(payload.get("chunk_id", "")).strip()
        if not chunk_id or chunk_id in existing_ids:
            continue
        injected = dict(payload)
        # Assign a synthetic retrieval_score so _rerank_with_query_tokens keeps
        # these items near the top.  repo_summary (priority=100) → 1.0;
        # other scored overview files get a proportional value.
        priority = _overview_priority(injected)
        injected.setdefault("retrieval_score", min(1.0, priority / 100.0))
        injected.setdefault("fusion_score", 0.0)
        # Mark as exact_retrieval_hit so the reranker always places overview
        # chunks above generic dense results (the reranker's primary sort key).
        injected["exact_retrieval_hit"] = True
        to_prepend.append(injected)
        existing_ids.add(chunk_id)

    return to_prepend + list(candidates)


def _repository_overview_candidates() -> list[dict]:
    """Fetch the repo-summary chunk (targeted) plus high-priority structured
    overview chunks (README, manifests, config files).

    Previously this scrolled only the first 400 Qdrant records (by UUID order),
    which missed the repo_summary chunk when the collection had >400 points.
    Now the repo_summary is fetched via a targeted chunk_type filter scroll,
    and the rest are fetched in a bounded secondary pass.
    """
    client = _get_client()
    collection = get_collection_name()

    # --- Step 1: targeted fetch of the repo_summary chunk (always 1 per repo)
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        repo_summary_response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(must=[
                FieldCondition(key="chunk_type", match=MatchValue(value="repo_summary"))
            ]),
            limit=3,
            with_payload=True,
        ))
    except Exception:
        repo_summary_response = None

    repo_summary_payloads: list[dict] = []
    if repo_summary_response is not None:
        rs_hits, _ = repo_summary_response
        repo_summary_payloads = [h.payload or {} for h in rs_hits if h.payload]

    # --- Step 2: scroll first 400 chunks for other high-priority overview files
    response = _qdrant_call(lambda: client.scroll(
        collection_name=collection,
        limit=400,
        with_payload=True,
    ))
    if response is None:
        # Fall back to repo_summary only if available
        return [p for p in repo_summary_payloads if p.get("chunk_id")]

    hits, _ = response
    payloads = [hit.payload or {} for hit in hits]
    payloads = [p for p in payloads if p.get("chunk_id") and p.get("chunk_type") != "repo_summary"]
    payloads.sort(
        key=lambda item: (
            -_overview_priority(item),
            item.get("relative_path", ""),
            int(item.get("start_line", 0)),
        )
    )

    chosen: list[dict] = []
    seen_files: set[str] = set()

    # Repo_summary always goes first (priority=100, handled separately)
    for rs_payload in repo_summary_payloads:
        if rs_payload.get("chunk_id"):
            chosen.append(rs_payload)
            seen_files.add("__repo_summary__.md")

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
    names: list[tuple[str, str]] = []

    # ES6/TS named imports: import { X, Y as Z } from 'module'
    match = re.search(r'import\s+\{([^}]+)\}\s+from\s+["\']([^"\']+)["\']', statement)
    if match:
        for part in match.group(1).split(","):
            cleaned = part.strip()
            if not cleaned:
                continue
            imported_name = cleaned.split(" as ", 1)[0].strip()
            if imported_name:
                names.append((imported_name, match.group(2).strip()))
        return names

    # ES6/TS mixed default + named imports: import Foo, { bar } from './mod'
    mixed_match = re.search(
        r'import\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*,\s*\{([^}]+)\}\s+from\s+["\']([^"\']+)["\']',
        statement,
    )
    if mixed_match:
        names.append((mixed_match.group(1).strip(), mixed_match.group(3).strip()))
        for part in mixed_match.group(2).split(","):
            cleaned = part.strip()
            if not cleaned:
                continue
            imported_name = cleaned.split(" as ", 1)[0].strip()
            if imported_name:
                names.append((imported_name, mixed_match.group(3).strip()))
        return names

    # ES6/TS namespace imports: import * as api from './api'
    ns_match = re.search(
        r'import\s+\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s+["\']([^"\']+)["\']',
        statement,
    )
    if ns_match:
        return [(ns_match.group(1).strip(), ns_match.group(2).strip())]

    # ES6/TS default import: import Foo from './foo'
    default_match = re.search(
        r'import\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s+["\']([^"\']+)["\']',
        statement,
    )
    if default_match:
        return [(default_match.group(1).strip(), default_match.group(2).strip())]

    py_match = re.match(r"^from\s+([.\w]+)\s+import\s+(.+)$", statement.strip())
    if not py_match:
        return names

    module_path = py_match.group(1).strip()
    imports_part = py_match.group(2).strip().strip("()")
    for part in imports_part.split(","):
        cleaned = part.strip()
        if not cleaned or cleaned == "*":
            continue
        imported_name = cleaned.split(" as ", 1)[0].strip()
        if imported_name and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", imported_name):
            names.append((imported_name, module_path))
    return names


def _resolve_import_relative_path(source_relative_path: str, module_path: str) -> str | None:
    repo_root = Path(get_repo_root())
    source_path = repo_root / source_relative_path

    if module_path.startswith("@/"):
        base = repo_root / "src" / module_path[2:]
    elif module_path.startswith("./") or module_path.startswith("../"):
        base = (source_path.parent / module_path).resolve()
    elif module_path.startswith("."):
        dot_count = len(module_path) - len(module_path.lstrip("."))
        remainder = module_path[dot_count:]
        package_root = source_path.parent
        for _ in range(max(0, dot_count - 1)):
            package_root = package_root.parent
        base = package_root / remainder.replace(".", "/") if remainder else package_root
    elif re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", module_path):
        base = repo_root / module_path.replace(".", "/")
    else:
        return None

    candidates = [
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.with_suffix(".js"),
        base.with_suffix(".jsx"),
        base.with_suffix(".py"),
        base.with_suffix(".json"),
        base / "index.ts",
        base / "index.tsx",
        base / "index.js",
        base / "index.jsx",
        base / "__init__.py",
        base,
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                return str(candidate.relative_to(repo_root))
            except ValueError:
                return None
    return None


def _fetch_import_symbol_chunks(
    relative_path: str,
    symbol_name: str,
    *,
    _visited: set[tuple[str, str]] | None = None,
    _depth: int = 0,
) -> list[dict]:
    visited = _visited or set()
    key = (relative_path, symbol_name)
    if key in visited or _depth >= IMPORT_TRACE_DEPTH_LIMIT:
        return []
    visited.add(key)

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
    payloads = [hit.payload or {} for hit in hits]
    if payloads:
        return payloads

    repo_root = Path(get_repo_root())
    source_path = repo_root / relative_path
    if source_path.suffix.lower() == ".json":
        payload = _build_imported_json_payload(source_path, relative_path, symbol_name)
        return [payload] if payload else []

    try:
        lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for target_symbol, target_module in _parse_re_exports(lines, symbol_name):
        resolved = _resolve_import_relative_path(relative_path, target_module)
        if not resolved:
            continue
        nested = _fetch_import_symbol_chunks(
            resolved,
            target_symbol,
            _visited=visited,
            _depth=_depth + 1,
        )
        if nested:
            return nested
    return []


def _parse_re_exports(lines: list[str], identifier: str) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    target = identifier.strip()
    if not target:
        return matches

    for line in lines:
        stripped = line.strip().rstrip(";")

        named = re.match(r'export\s+\{([^}]+)\}\s+from\s+["\']([^"\']+)["\']', stripped)
        if named:
            module_path = named.group(2).strip()
            for part in named.group(1).split(","):
                cleaned = part.strip()
                if not cleaned:
                    continue
                if " as " in cleaned:
                    source_name, exported_name = [item.strip() for item in cleaned.split(" as ", 1)]
                else:
                    source_name = exported_name = cleaned
                if exported_name == target:
                    matches.append(((target if source_name == "default" else source_name), module_path))

        wildcard = re.match(r'export\s+\*\s+from\s+["\']([^"\']+)["\']', stripped)
        if wildcard:
            matches.append((target, wildcard.group(1).strip()))

    return matches


def _build_imported_json_payload(path: Path, relative_path: str, imported_name: str) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    excerpt = raw.strip()
    if not excerpt:
        return None
    preview_lines = excerpt.splitlines()
    trimmed = "\n".join(preview_lines[:40]).rstrip()
    if len(preview_lines) > 40:
        trimmed += "\n..."

    summary = f"Imported JSON data from {relative_path}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        keys = [str(key) for key in list(parsed)[:6]]
        if keys:
            summary += f" with keys: {', '.join(keys)}"
    elif isinstance(parsed, list):
        summary += f" with {len(parsed)} top-level items"

    return {
        "chunk_id": f"imported-json::{relative_path}::{imported_name}",
        "relative_path": relative_path,
        "symbol_name": imported_name,
        "start_line": 1,
        "end_line": min(len(preview_lines), 40),
        "chunk_type": "file_summary",
        "file_type": "json",
        "summary": summary,
        "content": trimmed,
        "source": trimmed,
    }


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


# Intents that should always receive repo-summary + structured overview evidence.
_OVERVIEW_INTENTS: frozenset[str] = frozenset({"OVERVIEW", "TECH_STACK", "ARCHITECTURE"})


def _is_overview_intent(primary_intent: str) -> bool:
    """Return True for intents that always warrant repo-summary injection."""
    return primary_intent in _OVERVIEW_INTENTS


def _is_overview_query(raw_query: str) -> bool:
    q = raw_query.lower()
    return any(
        phrase in q
        for phrase in (
            "what is this project about",
            "whats this project about",
            "project overview",
            "overview of the project",
            "overview of this",
            "what does this project do",
            "what does this app do",
            "what does this codebase do",
            "what does this repo do",
            "give me an overview",
            "repo overview",
            "tech stack",
            "what framework",
            "what frameworks",
            "what stack",
            "which framework",
            "what dependencies",
            "main dependencies",
            "what services",
            "which services",
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
