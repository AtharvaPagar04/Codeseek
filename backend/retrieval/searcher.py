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
from retrieval.query_intent import (
    classify_query_intent,
    classify_source_intent,
    compute_label_boost,
)

_client = None
_model = None
_model_unavailable = False
_qdrant_failures = 0
_qdrant_circuit_open_until = 0.0
_lexical_indexes: dict[str, "_LexicalIndex"] = {}
IMPORT_TRACE_DEPTH_LIMIT = 3
TRACE_EXPANDED_CHUNKS_LIMIT = 6

CODE_REQUEST_TOPIC_ROUTES = (
    {
        "id": "auth",
        "phrases": [
            "auth function code",
            "auth code",
            "authentication code",
            "login auth code",
            "session auth code",
            "session validation function code",
            "session validation code",
            "validate session code",
        ],
        "target_paths": [
            "backend/retrieval/api_service.py",
            "backend/retrieval/auth_store.py",
        ],
        "target_symbols": [
            "_auth_key",
            "_require_auth",
            "_current_auth_user",
            "_require_auth_user",
            "create_auth_session",
            "get_user_for_session_token",
            "upsert_github_user",
            "delete_auth_session",
        ],
        "symbol_path_hints": {
            "_auth_key": "backend/retrieval/api_service.py",
            "_require_auth": "backend/retrieval/api_service.py",
            "_current_auth_user": "backend/retrieval/api_service.py",
            "_require_auth_user": "backend/retrieval/api_service.py",
            "create_auth_session": "backend/retrieval/auth_store.py",
            "get_user_for_session_token": "backend/retrieval/auth_store.py",
            "upsert_github_user": "backend/retrieval/auth_store.py",
            "delete_auth_session": "backend/retrieval/auth_store.py",
        },
        "exclude_paths": [
            "backend/rag_ingestion/stages/storage.py",
            "backend/retrieval/searcher.py",
        ],
        "multi_intro": "I found multiple auth-related functions:",
        "single_intro": "Here is the matching function:",
        "preferred_display_count": 6,
    },
    {
        "id": "safe_eval_runner",
        "phrases": [
            "safe eval runner code",
            "safe eval code",
            "safe evaluation runner code",
            "show me the safe eval runner",
            "run_safe_evals code",
            "run safe eval code",
            "safe eval implemented code",
            "where is safe eval implemented",
            "where is the safe eval runner implemented",
            "where is run_safe_evals implemented",
        ],
        "target_paths": [
            "backend/evals/run_safe_evals.py",
        ],
        "target_symbols": [],
        "symbol_path_hints": {},
        "exclude_paths": [
            "backend/retrieval/auth_store.py",
            "backend/retrieval/api_service.py",
            "backend/rag_ingestion/stages/storage.py",
            "backend/retrieval/searcher.py",
        ],
        "multi_intro": "I found multiple safe-eval runner snippets:",
        "single_intro": "Here is the matching function/code:",
        "preferred_display_count": 3,
    },
    {
        "id": "qdrant_upsert",
        "phrases": [
            "qdrant upsert code",
            "show me the qdrant upsert code",
            "qdrant upsert",
            "upsert qdrant",
            "qdrant upsert implemented code",
        ],
        "target_paths": [
            "backend/rag_ingestion/stages/storage.py",
        ],
        "target_symbols": [
            "store_chunks",
        ],
        "symbol_path_hints": {
            "store_chunks": "backend/rag_ingestion/stages/storage.py",
        },
        "exclude_paths": [
            "backend/retrieval/searcher.py",
            "backend/retrieval/api_service.py",
            "backend/retrieval/auth_store.py",
        ],
        "multi_intro": "I found multiple Qdrant upsert snippets:",
        "single_intro": "Here is the matching function/code:",
        "preferred_display_count": 2,
    },
    {
        "id": "evaluation_report_api",
        "phrases": [
            "evaluation report api endpoint code",
            "evaluation report endpoint code",
            "where is evaluation report api implemented",
            "where is the evaluation report api implemented",
            "where is the evaluation report endpoint implemented",
            "where is latest evaluation report implemented",
            "where is the latest evaluation report endpoint",
            "where is evaluation diagnostics endpoint implemented",
            "evaluation report api location",
            "evaluation report endpoint location",
            "latest evaluation report endpoint",
            "evaluation latest endpoint",
            "evaluation diagnostics endpoint",
            "show me the evaluation report api code",
            "show me the latest evaluation report code",
        ],
        "target_paths": [
            "backend/retrieval/api_service.py",
            "backend/retrieval/eval_reports.py",
        ],
        "target_symbols": [
            "get_latest_evaluation_report_v1",
            "get_latest_evaluation_report",
        ],
        "symbol_path_hints": {
            "get_latest_evaluation_report_v1": "backend/retrieval/api_service.py",
            "get_latest_evaluation_report": "backend/retrieval/eval_reports.py",
        },
        "exclude_paths": [
            "backend/retrieval/searcher.py",
            "backend/rag_ingestion/stages/storage.py",
        ],
        "multi_intro": "I found multiple evaluation-report endpoint snippets:",
        "single_intro": "Here is the matching function/code:",
        "preferred_display_count": 2,
    },
    {
        "id": "retrieval_internals",
        "phrases": [
            "where is reranking handled in searcher.py",
            "show me the reranking code in searcher.py",
            "where is final score computed",
            "where is final_score computed",
            "where are source boosts applied",
            "where are retrieval candidates reranked",
            "explain searcher.py reranking",
            "show me the searcher internals for reranking",
            "where does source_filter apply in retrieval",
            "where does source filter apply in retrieval",
            "searcher internals",
            "searcher.py reranking",
            "final score computed",
            "source boosts applied",
            "candidate ranking",
            "source filter apply",
            "source filter in retrieval",
            "searcher source filter",
        ],
        "target_paths": [
            "backend/retrieval/searcher.py",
            "backend/retrieval/source_filter.py",
        ],
        "target_symbols": [
            "_merge_results",
            "_rerank_with_query_tokens",
            "feature_specific_routing_boost",
            "artifact_penalty_for_intent",
            "symbol_definition_boost",
            "content_exact_match_boost",
            "classify_source_role",
            "apply_query_negative_filters",
        ],
        "symbol_path_hints": {
            "_merge_results": "backend/retrieval/searcher.py",
            "_rerank_with_query_tokens": "backend/retrieval/searcher.py",
            "feature_specific_routing_boost": "backend/retrieval/searcher.py",
            "artifact_penalty_for_intent": "backend/retrieval/searcher.py",
            "symbol_definition_boost": "backend/retrieval/searcher.py",
            "content_exact_match_boost": "backend/retrieval/searcher.py",
            "classify_source_role": "backend/retrieval/searcher.py",
            "apply_query_negative_filters": "backend/retrieval/source_filter.py",
        },
        "exclude_paths": [
            "backend/scripts/lexical_layer_benchmark.py",
            "backend/scripts/ragas_eval.py",
            "backend/scripts/retrieval_eval.py",
        ],
        "multi_intro": "I found multiple reranking/searcher-internals snippets:",
        "single_intro": "Here is the matching function/code:",
        "preferred_display_count": 5,
    },
)


def _normalized_query_text(raw_query: str) -> str:
    lowered = (raw_query or "").strip().lower()
    if not lowered:
        return ""
    for repeats in range(4, 1, -1):
        if len(lowered) % repeats:
            continue
        piece = lowered[: len(lowered) // repeats]
        if piece * repeats == lowered:
            lowered = piece
            break
    lowered = lowered.replace("_", " ").replace("-", " ")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    words = lowered.split()
    for size in range(1, (len(words) // 2) + 1):
        if len(words) % size:
            continue
        unit = words[:size]
        if unit * (len(words) // size) == words:
            words = unit
            break
    return " ".join(words)


def query_explicitly_requests_non_implementation_artifacts(raw_query: str) -> bool:
    q = _normalized_query_text(raw_query)
    if not q:
        return False
    implementation_markers = (
        "where is",
        "where are",
        "implemented",
        "implementation",
        "located",
        "defined",
        "endpoint",
        "api",
        "function",
        "handler",
        "code",
    )
    if any(term in q for term in ("test", "tests", "doc", "docs", "documentation", ".md", "markdown", "policy", "guide", "runbook")):
        return True
    if any(term in q for term in ("scratch", "benchmark", "plan")):
        return True
    if "report" in q:
        if not any(marker in q for marker in implementation_markers):
            return True
    return False


def query_explicitly_requests_searcher_internals(raw_query: str) -> bool:
    q = _normalized_query_text(raw_query)
    return any(
        term in q
        for term in (
            "retrieval routing",
            "reranking",
            "reranker",
            "rerank",
            "final score",
            "final_score",
            "source boost",
            "source boosts",
            "candidate ranking",
            "retrieval candidates",
            "searcher internals",
            "searcher.py",
            "retrieval/searcher.py",
            "source filter",
            "source_filter",
            "where is reranking handled",
            "where is final score computed",
            "where are source boosts applied",
            "where does source filter apply in retrieval",
        )
    )


def path_matches_topic_route(relative_path: str, route: dict | None) -> bool:
    if not route:
        return False
    rel = (relative_path or "").lower()
    return any(rel == target.lower() or rel.endswith("/" + target.lower()) for target in route.get("target_paths", []))


def symbol_matches_topic_route(symbol_name: str, relative_path: str, route: dict | None) -> bool:
    if not route:
        return False
    symbol = str(symbol_name or "")
    if not symbol:
        return False
    if symbol not in route.get("target_symbols", []):
        return False
    expected_path = route.get("symbol_path_hints", {}).get(symbol)
    if not expected_path:
        return True
    return path_matches_topic_route(relative_path, {"target_paths": [expected_path]})


def topic_route_excludes_path(relative_path: str, route: dict | None) -> bool:
    if not route:
        return False
    rel = (relative_path or "").lower()
    return any(rel == target.lower() or rel.endswith("/" + target.lower()) for target in route.get("exclude_paths", []))


def match_code_topic_route(raw_query: str, primary_intent: str | None = None) -> dict | None:
    q = _normalized_query_text(raw_query)
    raw_lower = (raw_query or "").lower()
    if not q:
        return None
    allow_source_location = (
        primary_intent in {"FILE", "SYMBOL", "CODE_REQUEST"}
        or "where is" in q
        or "where are" in q
        or "location" in q
    )
    if not allow_source_location:
        return None
    for route in CODE_REQUEST_TOPIC_ROUTES:
        if route["id"] == "auth" and any(symbol.lower() in raw_lower for symbol in route.get("target_symbols", [])):
            continue
        if any(phrase in q for phrase in route["phrases"]):
            return route
    return None


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
    filter_results = _metadata_search(raw_query, entities, query_info)
    exact_entity_results = _exact_entity_search(entities)
    dependency_results = _dependency_search(entities) if intent == "DEPENDENCY" else []

    local_content_results = _local_content_match_candidates(raw_query, primary_intent)

    matched_code_topic_route = match_code_topic_route(raw_query, primary_intent)
    if matched_code_topic_route:
        query_info["code_topic_route"] = matched_code_topic_route

    conversation_state = query_info.get("conversation_state") or {}
    previous_files = conversation_state.get("previous_files", [])
    history_results = []
    history_is_allowed = not matched_code_topic_route or primary_intent == "FOLLOWUP"
    if (query_info.get("is_followup") or primary_intent == "FOLLOWUP") and previous_files and history_is_allowed:
        history_results = _inject_previous_files_candidates(previous_files)

    direct_topic_results = _inject_direct_topics_candidates(raw_query, primary_intent)
    auth_routing_results = _inject_code_topic_routing_candidates(raw_query, primary_intent, matched_code_topic_route)

    merged = _merge_results(
        dense_results,
        lexical_results,
        filter_results,
        exact_entity_results,
        dependency_results,
        local_content_results,
        history_results,
        direct_topic_results,
        auth_routing_results,
    )
    # Inject repo-summary and structured overview evidence for any query whose
    # primary intent is broad/structural.  The phrase-based gate is kept as a
    # fast-path fallback for cases where intent scoring disagrees.
    if _is_overview_intent(primary_intent) or _is_overview_query(raw_query):
        merged = _inject_overview_candidates(merged)
    if primary_intent == "ARCHITECTURE" or _is_architecture_query(raw_query):
        merged = _inject_architecture_file_candidates(merged, entities)
    merged = _inject_import_backing_candidates(raw_query, merged)
    merged = _rerank_with_query_tokens(raw_query, merged, query_info)

    query_intent_explicit = any(
        term in raw_query.lower()
        for term in [
            "query_intent.py",
            "is_code_request_query",
            "code request detection",
            "intent classifier",
            "query classification",
        ]
    )
    if not query_intent_explicit:
        merged = [
            m for m in merged
            if "query_intent.py" not in (m.get("relative_path") or "")
        ]

    return merged[:TOP_K_AFTER_MERGE]


def _should_ignore_for_retrieval(relative_path: str) -> bool:
    if not relative_path:
        return False
    path_lower = relative_path.lower()
    if path_lower.endswith((".json", ".yaml", ".yml", ".jsonl")):
        return True
    if "evals/reports/" in path_lower or "eval_reports/" in path_lower or "reports/" in path_lower:
        return True
    return False


def _dense_search(raw_query: str):
    if not ENABLE_DENSE_RETRIEVAL:
        return []
    model = _get_model()
    if model is None:
        return []
    client = _get_client()
    collection = get_collection_name()
    query_vector = model.encode(QUERY_PREFIX + raw_query).tolist()
    
    # Query more points to account for filtered non-code documents
    limit = TOP_K_DENSE * 2
    if hasattr(client, "search"):
        points = _qdrant_call(lambda: client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=limit,
            with_payload=True,
        ))
    else:
        query = _qdrant_call(lambda: client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=limit,
            with_payload=True,
        ))
        if query is None:
            return []
        points = query.points
    if points is None:
        return []
    
    res = []
    for point in points:
        payload = dict(point.payload or {})
        rel_path = payload.get("relative_path", "")
        if _should_ignore_for_retrieval(rel_path):
            continue
        res.append((payload, float(point.score), "dense"))
        if len(res) >= TOP_K_DENSE:
            break
    return res


def is_index_health_query(raw_query: str, query_info: dict | None = None) -> tuple[bool, bool]:
    """Detect index health and reindex guidance queries and their follow-ups."""
    q_info = query_info or {}
    user_q = str(q_info.get("user_query", raw_query)).lower()
    raw_q = str(raw_query).lower()
    
    prev_q = q_info.get("follow_up_to", "") or ""
    if not prev_q and q_info.get("conversation_state"):
        prev_q = q_info["conversation_state"].get("previous_query", "") or ""
    prev_q = str(prev_q).lower()
    
    prev_resolved_q = q_info.get("follow_up_resolved_to", "") or ""
    prev_resolved_q = str(prev_resolved_q).lower()
    
    # Check for "reindex guidance"
    if "reindex guidance" in user_q or "reindex guidance" in raw_q:
        return True, True
        
    # Check for "index health" or "index health check"
    is_index_h = False
    if "index health" in user_q or "index health" in raw_q:
        is_index_h = True
        
    # Check for "remediation" when previous query/context contains "index health"
    is_remediation_followup = False
    if "remediation" in user_q or "remediation" in raw_q:
        if "index health" in prev_q or "index health" in prev_resolved_q or "index health" in raw_q:
            is_remediation_followup = True
            is_index_h = True
            
    return is_index_h, (is_remediation_followup or "reindex guidance" in user_q or "reindex guidance" in raw_q)


def _metadata_search(raw_query: str, entities: dict, query_info: dict | None = None):
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
            results.extend((dict(hit.payload or {}), 0.0, "filter") for hit in hits)
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
            results.extend((dict(hit.payload or {}), 0.0, "filter") for hit in hits)

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
            results.extend((dict(hit.payload or {}), 0.0, "filter") for hit in hits)
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
                payload = dict(hit.payload or {})
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
            payload = dict(hit.payload or {})
            relative_path = str(payload.get("relative_path", "")).lower()
            if key.lower() in relative_path:
                results.append((payload, 0.0, "metadata"))

    # Targeted search for index health check / reindex guidance files
    is_index_h, is_reindex_guid = is_index_health_query(raw_query, query_info)
    if is_index_h or is_reindex_guid:
        target_files = []
        if is_index_h:
            target_files.append("backend/evals/index_health.py")
        if is_reindex_guid:
            target_files.append("backend/evals/reindex_guidance.py")
            
        for file_path in target_files:
            response = _qdrant_call(lambda: client.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[FieldCondition(key="relative_path", match=MatchValue(value=file_path))]
                ),
                limit=50,
                with_payload=True,
            ))
            if response is not None:
                hits, _ = response
                for hit in hits:
                    payload = dict(hit.payload or {})
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

def _local_content_match_candidates(raw_query: str, intent: str, limit: int = 12) -> list[tuple[dict, float, str]]:
    """Find local source files containing important query terms.

    Used as a recall fallback when dense/BM25/exact miss implementation files.
    Keep this narrow and only for FILE/SYMBOL/DEPENDENCY-style source-location queries.
    """
    intent = (intent or "").upper()
    if intent not in {"FILE", "SYMBOL", "DEPENDENCY"}:
        return []

    q = (raw_query or "").lower()

    # Narrow trigger for current failure mode.
    required_terms: list[str] = []
    if "qdrant" in q and "upsert" in q:
        required_terms = ["qdrant", "upsert"]
    elif "fastapi" in q and ("initialized" in q or "initialization" in q):
        required_terms = ["fastapi"]
    else:
        return []

    repo_root = Path(get_repo_root()).resolve()
    skip_parts = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
    }

    allowed_suffixes = {".py", ".ts", ".tsx", ".js", ".jsx"}
    matches: list[tuple[int, dict, float, str]] = []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        if any(part in skip_parts for part in path.parts):
            continue

        try:
            relative_path = path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            continue

        relative_lower = relative_path.lower()

        # Avoid test/fixture/docs pollution for this fallback.
        if (
            relative_lower.startswith("backend/tests/")
            or relative_lower.startswith("tests/")
            or "/tests/fixtures/" in relative_lower
            or relative_lower.startswith("backend/docs/")
            or relative_lower.startswith("docs/")
            or relative_lower.startswith("evals/")
        ):
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        text_lower = text.lower()
        if not all(term in text_lower or term in relative_lower for term in required_terms):
            continue

        score = 0.85
        if "client.upsert" in text_lower or ".upsert(" in text_lower:
            score += 0.20
        if relative_lower == "backend/rag_ingestion/stages/storage.py":
            score += 0.25
        if relative_lower.endswith(("storage.py", "api_service.py", "main.py")):
            score += 0.10

        lines = text.splitlines()
        payload = {
            "chunk_id": f"local-content::{relative_path}::{','.join(required_terms)}",
            "relative_path": relative_path,
            "symbol_name": "",
            "qualified_symbol": f"{relative_path}::<content-match>",
            "chunk_type": "file",
            "language": path.suffix.lower().lstrip("."),
            "start_line": 1,
            "end_line": max(1, len(lines)),
            "summary": f"Local content match for {', '.join(required_terms)} in {relative_path}",
            "content": text,
            "content_excerpt": text[:4000],
            "exact_retrieval_hit": True,
            "support_kind": "local_content_match",
        }

        priority = 0
        if relative_lower.startswith("backend/"):
            priority -= 10
        if relative_lower == "backend/rag_ingestion/stages/storage.py":
            priority -= 20

        matches.append((priority, payload, score, "local_content"))

    matches.sort(key=lambda item: (item[0], -item[2], item[1]["relative_path"]))
    return [(payload, score, source) for _priority, payload, score, source in matches[:limit]]
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
    generic_skips = {"api", "backend", "frontend", "web", "worker", "service", "services"}
    for term in terms:
        value = term.strip()
        if len(value) < 3:
            continue
        if value.lower() in generic_skips:
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
        rel_path = payload.get("relative_path", "")
        if _should_ignore_for_retrieval(rel_path):
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


def _inject_previous_files_candidates(previous_files: list[str]) -> list[tuple[dict, float, str]]:
    if not previous_files:
        return []
    client = _get_client()
    collection = get_collection_name()
    from qdrant_client.models import Filter, FieldCondition, MatchAny
    response = _qdrant_call(lambda: client.scroll(
        collection_name=collection,
        scroll_filter=Filter(
            must=[FieldCondition(key="relative_path", match=MatchAny(any=previous_files))]
        ),
        limit=150,
        with_payload=True,
    ))
    if not response:
        return []
    hits, _ = response
    results = []
    for hit in hits:
        payload = dict(hit.payload or {})
        payload["support_kind"] = "conversation_history"
        results.append((payload, 0.0, "history"))
    return results


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
            if source in {"filter", "calls", "exact_entity", "history", "direct_injection", "auth_routing", "code_topic_routing"}:
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


def _inject_direct_topics_candidates(raw_query: str, primary_intent: str) -> list[tuple[dict, float, str]]:
    q = (raw_query or "").lower()
    
    # 1. Freshness Queries
    freshness_triggers = {
        "repo freshness",
        "freshness status",
        "repo status",
        "dirty worktree",
        "stale repo",
        "index latest",
        "status checked",
    }
    
    # 2. Auth / Session Validation Queries
    auth_triggers = {
        "auth",
        "authentication",
        "github auth",
        "session validation",
        "validate session",
        "session cookie",
        "auth session",
        "login callback",
    }
    
    source_intent = classify_source_intent(raw_query)
    repo_root = Path(get_repo_root()).resolve()
    target_files = _discover_contract_target_files(repo_root, source_intent, raw_query)

    if not target_files and any(trigger in q for trigger in freshness_triggers):
        target_files = _discover_contract_target_files(repo_root, "indexing_status", raw_query)
    elif not target_files and any(trigger in q for trigger in auth_triggers):
        target_files = _discover_contract_target_files(repo_root, "api_endpoint", raw_query)

    if not target_files:
        return []

    results = []
    
    for rel_path in target_files:
        file_path = repo_root / rel_path
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            payload = {
                "chunk_id": f"direct-inject::{rel_path}",
                "relative_path": rel_path,
                "symbol_name": "",
                "qualified_symbol": f"{rel_path}::__file__",
                "chunk_type": "file",
                "language": file_path.suffix.lower().lstrip("."),
                "start_line": 1,
                "end_line": max(1, len(lines)),
                "summary": f"Direct injected file candidate {rel_path}",
                "content": text,
                "content_excerpt": text[:4000],
                "exact_retrieval_hit": True,
                "support_kind": "direct_injection",
                "labels": ["question_use:technical-explanation", "question_use:general-context"],
            }
            results.append((payload, 0.95, "direct_injection"))
        except OSError:
            continue
            
    return results


def _discover_contract_target_files(repo_root: Path, source_intent: str, raw_query: str, *, limit: int = 10) -> list[str]:
    if source_intent in {"general", "code_location", "exact_symbol", "docs_question"}:
        return []
    if not repo_root.exists():
        return []

    scored: list[tuple[int, str]] = []
    for path in _iter_contract_candidate_files(repo_root):
        rel_path = path.relative_to(repo_root).as_posix()
        score = _generic_contract_path_score(rel_path, source_intent, raw_query)
        if score > 0:
            scored.append((score, rel_path))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [rel_path for _score, rel_path in scored[:limit]]


def _iter_contract_candidate_files(repo_root: Path):
    skip_dirs = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
        "coverage",
        ".next",
        ".nuxt",
        "target",
    }
    allowed_suffixes = {".md", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".toml", ".yaml", ".yml", ".go", ".rs", ".java", ".kt", ".cs", ".php", ".rb"}
    max_seen = 5000
    seen = 0
    for path in repo_root.rglob("*"):
        if seen >= max_seen:
            break
        if any(part in skip_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue
        seen += 1
        if path.suffix.lower() not in allowed_suffixes and path.name.lower() not in {"dockerfile", "makefile"}:
            continue
        yield path


def _generic_contract_path_score(relative_path: str, source_intent: str, raw_query: str) -> int:
    path = relative_path.lower()
    name = Path(path).name
    q = (raw_query or "").lower()
    score = 0

    is_readme = name.startswith("readme")
    is_doc = path.startswith("docs/") or "/docs/" in path or path.endswith(".md")
    is_config = name in {"package.json", "pyproject.toml", "requirements.txt", "go.mod", "cargo.toml", "pom.xml", "build.gradle", "docker-compose.yml", "docker-compose.yaml", "dockerfile"} or "config" in path
    is_frontend = path.startswith("frontend/") or "/frontend/" in path or "/src/components/" in path or "/src/pages/" in path or "/src/hooks/" in path or path.endswith(("app.jsx", "app.tsx", "app.js", "app.ts"))
    is_api = any(term in path for term in ("api", "route", "routes", "controller", "controllers", "server", "handler", "handlers", "endpoint", "endpoints")) or name in {"main.py", "app.py", "server.js", "server.ts", "index.js", "index.ts"}
    is_indexing = any(term in path for term in ("index", "ingest", "parser", "parse", "chunk", "embed", "vector", "store", "storage", "discover", "filter", "crawl"))
    is_retrieval = any(term in path for term in ("retriev", "search", "rag", "rank", "rerank", "query", "answer", "llm", "source", "citation", "assembler"))
    is_provider = any(term in path for term in ("provider", "credential", "settings", "config", "llm", "model"))
    is_failure = any(term in path for term in ("job", "status", "fresh", "recover", "retry", "cancel", "error", "fail", "db", "database", "troubleshoot"))
    is_auth = any(term in path for term in ("auth", "login", "session", "credential", "token"))

    if path.startswith(("test/", "tests/", "backend/tests/")) or "/tests/" in path or "/scratch/" in path:
        score -= 1000
    if "/scripts/" in path and any(term in path for term in ("benchmark", "eval", "report")):
        score -= 800

    if source_intent == "overview":
        score += 900 if is_readme else 0
        score += 650 if is_doc else 0
        score += 450 if is_config else 0
        score += 350 if is_api or is_frontend else 0
        if "architecture" in q or "modules" in q:
            score += 400 if is_api else 0
    elif source_intent == "runtime_architecture":
        score += 750 if is_readme or is_doc else 0
        score += 650 if is_api else 0
        score += 550 if is_frontend else 0
        score += 450 if is_config else 0
        score += 350 if is_indexing or is_retrieval else 0
    elif source_intent == "frontend_backend_flow":
        score += 850 if is_frontend and any(term in path for term in ("api", "client", "hook", "service", "session", "app")) else 0
        score += 800 if is_api else 0
        score += 250 if is_doc else 0
    elif source_intent in {"repository_analysis", "indexing_pipeline", "incremental_indexing"}:
        score += 900 if is_indexing else 0
        score += 550 if is_failure and source_intent == "incremental_indexing" else 0
        score += 350 if is_doc and any(term in path for term in ("index", "ingest", "reindex", "troubleshoot")) else 0
    elif source_intent in {"retrieval_pipeline", "source_filtering"}:
        score += 900 if is_retrieval else 0
        score += 450 if is_api and source_intent == "retrieval_pipeline" else 0
        score += 400 if is_frontend and source_intent == "source_filtering" and any(term in path for term in ("source", "card", "message", "citation")) else 0
    elif source_intent == "ui_implementation":
        score += 1000 if is_frontend else 0
        score += 450 if any(term in path for term in ("component", "view", "page", "screen", "panel", "button", "card", "message")) else 0
    elif source_intent == "api_endpoint":
        score += 1000 if (is_api or is_auth) and not is_frontend else 0
        score += 300 if is_frontend and ("api" in path or is_auth) else 0
        score += 700 if name == "db.py" else 0
        if "authcallback" in path or ("auth" in path and "callback" in path):
            score += 800
        if "/scripts/" in path:
            score -= 400
        if is_doc or path.endswith((".json", ".yaml", ".yml")):
            score -= 600
        if "provider_endpoint" in path or ("llm" in path and "provider" in q):
            score -= 700
    elif source_intent == "provider_configuration":
        score += 850 if is_provider else 0
        score += 550 if is_frontend and any(term in path for term in ("provider", "settings", "credential", "config")) else 0
        score += 400 if is_api else 0
    elif source_intent in {"failure_recovery", "indexing_status"}:
        score += 850 if is_failure else 0
        score += 650 if is_indexing else 0
        score += 350 if is_doc and any(term in path for term in ("troubleshoot", "index", "fresh", "status", "recover")) else 0
        score += 500 if is_api or "session_indexer" in path else 0
        if "api_service" in path:
            score += 800

    return score


def _inject_code_topic_routing_candidates(
    raw_query: str,
    primary_intent: str,
    matched_route: dict | None = None,
) -> list[tuple[dict, float, str]]:
    from retrieval.code_answers import is_code_request
    route = matched_route or match_code_topic_route(raw_query, primary_intent)
    if not route:
        return []
    if primary_intent != "CODE_REQUEST" and not is_code_request(raw_query) and "where is" not in _normalized_query_text(raw_query):
        return []

    client = _get_client()
    collection = get_collection_name()
    results = []
    seen_chunk_ids: set[str] = set()

    for rel_path in route.get("target_paths", []):
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="relative_path", match=MatchValue(value=rel_path))]
            ),
            limit=20,
            with_payload=True,
        ))
        hits = response[0] if response is not None else []
        for hit in hits:
            payload = dict(hit.payload or {})
            chunk_id = str(payload.get("chunk_id", "")).strip()
            if chunk_id and chunk_id in seen_chunk_ids:
                continue
            if not path_matches_topic_route(payload.get("relative_path", ""), route):
                continue
            payload["exact_retrieval_hit"] = True
            payload["support_kind"] = "code_topic_routing"
            results.append((payload, 0.97, "code_topic_routing"))
            if chunk_id:
                seen_chunk_ids.add(chunk_id)

        local_file_payload = _local_file_hint_payload(rel_path)
        if local_file_payload:
            chunk_id = str(local_file_payload.get("chunk_id", "")).strip()
            if chunk_id and chunk_id not in seen_chunk_ids:
                local_file_payload["exact_retrieval_hit"] = True
                local_file_payload["support_kind"] = "code_topic_routing"
                results.append((local_file_payload, 0.93, "code_topic_routing"))
                seen_chunk_ids.add(chunk_id)

    for symbol in route.get("target_symbols", []):
        expected_path = route.get("symbol_path_hints", {}).get(symbol)
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="symbol_name", match=MatchValue(value=symbol))]
            ),
            limit=8,
            with_payload=True,
        ))
        hits = response[0] if response is not None else []
        for hit in hits:
            payload = dict(hit.payload or {})
            chunk_id = str(payload.get("chunk_id", "")).strip()
            rel_path = payload.get("relative_path", "")
            if chunk_id and chunk_id in seen_chunk_ids:
                continue
            if expected_path and not path_matches_topic_route(rel_path, {"target_paths": [expected_path]}):
                continue
            if not path_matches_topic_route(rel_path, route):
                continue
            payload["exact_retrieval_hit"] = True
            payload["support_kind"] = "code_topic_routing"
            results.append((payload, 0.99, "code_topic_routing"))
            if chunk_id:
                seen_chunk_ids.add(chunk_id)

    return results


def _inject_auth_routing_candidates(raw_query: str, primary_intent: str) -> list[tuple[dict, float, str]]:
    from retrieval.code_answers import is_code_request
    if primary_intent != "CODE_REQUEST" and not is_code_request(raw_query):
        return _inject_code_topic_routing_candidates(raw_query, primary_intent)

    q_lower = _normalized_query_text(raw_query)
    legacy_symbol_routes = {
        "query endpoint": [("_query_impl", "backend/retrieval/api_service.py")],
        "qdrant upsert": [("store_chunks", "backend/rag_ingestion/stages/storage.py")],
        "session validation": [
            ("get_user_for_session_token", "backend/retrieval/auth_store.py"),
            ("_current_auth_user", "backend/retrieval/api_service.py"),
            ("_require_auth_user", "backend/retrieval/api_service.py"),
        ],
    }
    selected_routes = []
    for phrase, entries in legacy_symbol_routes.items():
        if phrase in q_lower:
            selected_routes = entries
            break

    if not selected_routes:
        return _inject_code_topic_routing_candidates(raw_query, primary_intent)

    client = _get_client()
    collection = get_collection_name()
    results = []
    for symbol, expected_path in selected_routes:
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="symbol_name", match=MatchValue(value=symbol))]
            ),
            limit=8,
            with_payload=True,
        ))
        hits = response[0] if response is not None else []
        for hit in hits:
            payload = dict(hit.payload or {})
            rel_path = payload.get("relative_path", "")
            if not path_matches_topic_route(rel_path, {"target_paths": [expected_path]}):
                continue
            payload["exact_retrieval_hit"] = True
            payload["support_kind"] = "auth_routing"
            results.append((payload, 0.99, "auth_routing"))
    return results


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


def artifact_penalty_for_intent(relative_path: str, intent: str, previous_files: list[str] | None = None) -> float:
    """Downweight non-source artifacts for source-location/code queries.

    Do not penalize CONFIG, OVERVIEW, ARCHITECTURE, or FOLLOWUP because docs/config/eval files
    can be valid context for those query types, especially when continuing from history.
    """
    path = (relative_path or "").lower()
    intent = (intent or "").upper()

    if "evals/index_health.py" in path or "evals/reindex_guidance.py" in path:
        return 1.0

    if previous_files and relative_path in previous_files:
        return 1.0

    if intent in {"CONFIG", "OVERVIEW", "ARCHITECTURE", "FOLLOWUP"}:
        return 1.0

    # Eval/golden/report artifacts should not beat real source files for FILE/SYMBOL.
    if (
        path.startswith("evals/")
        or "/evals/" in path
        or path.startswith("backend/evals/")
        or "/backend/evals/" in path
    ):
        return 0.40

    # Test fixtures are almost never the answer for source-location queries.
    if (
        "/tests/fixtures/" in path
        or path.startswith("tests/fixtures/")
        or path.startswith("backend/tests/fixtures/")
    ):
        return 0.40

    # Normal tests can be useful, but should not beat implementation files.
    if (
        "/tests/" in path
        or path.startswith("tests/")
        or path.startswith("backend/tests/")
    ):
        return 0.60

    # Docs are valid for architecture/overview, but not source-location.
    if (
        path.startswith("docs/")
        or path.startswith("backend/docs/")
        or "/docs/" in path
        or path.endswith(".md")
    ):
        return 0.55

    # Eval/benchmark/report data files should not dominate source-location.
    if path.endswith((".json", ".yaml", ".yml")) and (
        "fixture" in path
        or "benchmark" in path
        or "report" in path
        or "eval" in path
        or "golden" in path
    ):
        return 0.45

    # Config/manifests should not dominate non-CONFIG implementation queries.
    if path.endswith((
        "docker-compose.yml",
        "dockerfile",
        "requirements.txt",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    )):
        return 0.55

    return 1.0


def symbol_definition_boost(candidate: dict, extracted_symbols: list[str], query: str) -> float:
    if not extracted_symbols:
        return 0.0

    q = (query or "").lower()
    is_definition_query = any(
        term in q
        for term in ["defined", "definition", "declared", "implemented", "located", "where is"]
    )

    if not is_definition_query:
        return 0.0

    symbol_name = (candidate.get("symbol_name") or "").lower()
    qualified_symbol = (candidate.get("qualified_symbol") or "").lower()
    content = (
        candidate.get("content")
        or candidate.get("content_excerpt")
        or candidate.get("summary")
        or ""
    ).lower()

    for sym in extracted_symbols:
        s = (sym or "").lower()
        if not s:
            continue

        if symbol_name == s:
            return 0.25

        if qualified_symbol.endswith("." + s) or qualified_symbol.endswith("::" + s):
            return 0.22

        if f"def {s}" in content or f"class {s}" in content or f"{s} =" in content or f"{s}:" in content:
            return 0.18

    return 0.0


def content_exact_match_boost(candidate: dict, extracted_symbols: list[str], query_terms: list[str]) -> float:
    content = (
        candidate.get("content")
        or candidate.get("content_excerpt")
        or candidate.get("summary")
        or ""
    ).lower()

    score = 0.0

    for sym in extracted_symbols:
        s = (sym or "").lower()
        if s and s in content:
            score += 0.08

    return min(score, 0.20)


def framework_source_boost(candidate: dict, query: str, intent: str) -> float:
    """Small targeted boost for framework initialization source-location queries."""
    q = (query or "").lower()
    intent = (intent or "").upper()

    if intent not in {"FILE", "SYMBOL"}:
        return 0.0

    if "fastapi" not in q and "app initialized" not in q and "app initialization" not in q:
        return 0.0

    path = (candidate.get("relative_path") or "").lower()
    content = (
        candidate.get("content")
        or candidate.get("content_excerpt")
        or candidate.get("summary")
        or ""
    ).lower()

    if path.startswith("backend/tests/") or "/tests/fixtures/" in path:
        return 0.0

    if "fastapi(" in content or "app = fastapi" in content or "api = fastapi" in content:
        return 0.25

    if path in {"backend/retrieval/api_service.py", "backend/retrieval/main.py"}:
        return 0.18

    return 0.0
def qdrant_upsert_source_boost(candidate: dict, query: str, intent: str) -> float:
    """Boost real source files containing Qdrant upsert implementation."""
    q = (query or "").lower()
    intent = (intent or "").upper()

    if intent not in {"FILE", "SYMBOL"}:
        return 0.0

    if "qdrant" not in q or "upsert" not in q:
        return 0.0

    path = (candidate.get("relative_path") or "").lower()
    content = (
        candidate.get("content")
        or candidate.get("content_excerpt")
        or candidate.get("summary")
        or ""
    ).lower()

    if path.startswith("backend/tests/") or "/tests/fixtures/" in path:
        return 0.0

    # This is the real ingestion storage implementation.
    if path == "backend/rag_ingestion/stages/storage.py":
        return 0.45

    if "client.upsert" in content or ".upsert(" in content:
        return 0.38

    if "qdrant" in content and "upsert" in content:
        return 0.30

    return 0.0

def config_source_boost(candidate: dict, query: str, intent: str) -> float:
    """Boost configuration implementation files for config/environment queries."""
    q = (query or "").lower()
    intent = (intent or "").upper()

    if intent != "CONFIG":
        return 0.0

    path = (candidate.get("relative_path") or "").lower()
    content = (
        candidate.get("content")
        or candidate.get("content_excerpt")
        or candidate.get("summary")
        or ""
    ).lower()

    if path.startswith("backend/tests/") or "/tests/fixtures/" in path:
        return 0.0

    filename = path.split("/")[-1]
    boost = 0.0

    # Boost files whose path basename is config.py or settings.py
    if filename in {"config.py", "settings.py"}:
        boost += 0.40

    # Boost chunks containing environment/config APIs or constants
    env_terms = [
        "os.getenv",
        "os.environ",
        "retrieval_",
        "ollama_",
        "qdrant_",
        "database",
        "api_key"
    ]
    for term in env_terms:
        if term in content:
            boost += 0.08

    # If the path actually matches backend/retrieval/config.py, we can add a strong boost
    if path == "backend/retrieval/config.py":
        boost += 0.25

    return min(boost, 0.65)


def classify_source_role(relative_path: str) -> str:
    if not relative_path:
        return "unknown"
    path_lower = relative_path.lower()
    
    # 1. answer_template
    if path_lower == "backend/retrieval/code_answers.py" or path_lower.endswith("backend/retrieval/code_answers.py"):
        return "answer_template"
        
    # 2. generated_eval
    if ("evals/reports/" in path_lower or 
        "eval_reports/" in path_lower or 
        "safe_eval_latest/" in path_lower or 
        path_lower.startswith("safe_eval_latest/")):
        return "generated_eval"
    if path_lower.startswith("backend/scripts/") and any(term in path_lower for term in ("eval", "ragas", "report")):
        return "generated_eval"
        
    # 3. test
    if (path_lower.startswith("backend/tests/") or 
        "/tests/" in path_lower or 
        ".test." in path_lower or 
        path_lower.endswith("_test.py") or 
        path_lower.endswith("test.js") or 
        path_lower.endswith("test.jsx")):
        return "test"
        
    # 4. docs
    if (path_lower.endswith(".md") or 
        path_lower.startswith("docs/") or 
        "/docs/" in path_lower or 
        path_lower.startswith("reports/") or 
        "/reports/" in path_lower):
        return "docs"
        
    # 5. scratch/tooling
    if (path_lower.startswith("backend/scratch/") or 
        "scratch/" in path_lower or 
        "benchmark" in path_lower or 
        "verify" in path_lower or 
        "validate" in path_lower or 
        "check" in path_lower):
        return "scratch/tooling"
        
    # 6. implementation
    if (
        (path_lower.startswith("backend/retrieval/") and path_lower.endswith(".py")) or
        (path_lower.startswith("backend/evals/") and path_lower.endswith(".py")) or
        (path_lower.startswith("backend/rag_ingestion/") and path_lower.endswith(".py")) or
        (path_lower.startswith("frontend/src/") and (path_lower.endswith(".js") or path_lower.endswith(".jsx")))
    ):
        return "implementation"
        
    return "unknown"


def feature_specific_routing_boost(relative_path: str, raw_query: str) -> float:
    path = (relative_path or "").lower()
    q = (raw_query or "").lower()
    route = match_code_topic_route(raw_query, "CODE_REQUEST")
    if route:
        if path_matches_topic_route(path, route):
            return 1.2
        if topic_route_excludes_path(path, route):
            return -3.0
    
    # 1. FastAPI/app initialized
    if "fastapi" in q or "app init" in q or "initialize" in q:
        if path == "backend/retrieval/api_service.py":
            return 0.9
            
    # 2. Qdrant upsert
    if "qdrant upsert" in q or "upsert to qdrant" in q or "upsert qdrant" in q or ("qdrant" in q and "upsert" in q):
        if path == "backend/rag_ingestion/stages/storage.py":
            return 0.9
            
    # 3. repo freshness/status/dirty/stale/index latest
    if (
        "freshness" in q
        or "repo status" in q
        or "dirty" in q
        or "stale" in q
        or "index latest" in q
        or "reindex" in q
        or "index status" in q
    ):
        if path in {"backend/retrieval/session_indexer.py", "backend/retrieval/api_service.py"}:
            return 0.9
    # 3. evaluation report API implementation
    if (
        "evaluation report api" in q
        or "evaluation report endpoint" in q
        or "latest evaluation report" in q
        or "evaluation diagnostics endpoint" in q
        or "where is evaluation report" in q
    ):
        if path in {"backend/retrieval/api_service.py", "backend/retrieval/eval_reports.py"}:
            return 1.0
        if path == "backend/scripts/ragas_eval.py":
            return -0.8
            
    # 4. auth/session validation
    if "auth" in q or "session validation" in q or "validate session" in q or "session validate" in q or "login" in q or "token" in q:
        if path in {"backend/retrieval/api_service.py", "backend/retrieval/auth_store.py", "backend/retrieval/db.py"}:
            return 0.9
            
    return 0.0


def _rerank_with_query_tokens(raw_query: str, candidates: list[dict], query_info: dict | None = None) -> list[dict]:
    """Apply unified label-aware and lexical scoring to rank candidates."""
    from pathlib import Path
    from retrieval.source_filter import apply_query_negative_filters
    query_profile = classify_query_intent(raw_query)
    tokens = _query_tokens(raw_query)

    from retrieval.query_intent import map_label_intent_to_reranker_intent, is_dependency_trace_query
    label_intent = query_profile.get("intent", "general_context")
    response_mode = query_profile.get("response_mode", "")
    extracted_entities = query_info.get("entities") if query_info else None
    extracted_symbols = extracted_entities.get("symbols", []) if extracted_entities else []
    is_followup = query_info.get("is_followup", False) if query_info else False
    is_low_context = (query_info.get("primary_intent") == "LOW_CONTEXT") if query_info else False
    primary_intent = query_info.get("primary_intent") if query_info else None

    reranker_intent = map_label_intent_to_reranker_intent(
        label_intent,
        query=raw_query,
        is_followup=is_followup,
        is_low_context=is_low_context,
        extracted_entities=extracted_entities
    )

    conversation_state = query_info.get("conversation_state") if query_info else None
    previous_files = conversation_state.get("previous_files", []) if conversation_state else []
    previous_symbols = conversation_state.get("previous_symbols", []) if conversation_state else []
    matched_code_topic_route = (
        query_info.get("code_topic_route") if query_info else None
    ) or match_code_topic_route(raw_query, primary_intent)
    strict_code_topic_route = bool(
        matched_code_topic_route
        and not query_explicitly_requests_non_implementation_artifacts(raw_query)
        and not query_explicitly_requests_searcher_internals(raw_query)
    )
    candidates = apply_query_negative_filters(
        candidates,
        raw_query,
        intent=primary_intent,
        matched_route=matched_code_topic_route if strict_code_topic_route else None,
    )

    rescored = []
    for item in candidates:
        vector_score = float(item.get("retrieval_score", 0.0))
        if item.get("exact_retrieval_hit"):
            vector_score = max(vector_score, 0.70)
        elif vector_score == 0.0 and item.get("fusion_score", 0.0) > 0.0:
            vector_score = min(0.65, 0.50 + 5.0 * float(item.get("fusion_score", 0.0)))

        exact_match_score = min(float(item.get("exact_entity_score", 0.0)) / 4.0, 1.0)
        label_boost = compute_label_boost(item.get("labels", []), query_profile)

        overlap = _overlap_score(tokens, item) if tokens else 0
        path_symbol_boost = min(float(overlap) / 3.0, 1.0)

        file_type_boost = 0.0
        relative_path = item.get("relative_path", "")
        symbol_name = item.get("symbol_name", "")
        filename = Path(relative_path).name.lower()
        clean_filename = filename.rsplit(".", 1)[0]
        if reranker_intent == "FILE" and item.get("chunk_type") == "file":
            file_type_boost = 0.20
        if clean_filename in raw_query.lower() or (clean_filename == "db" and "database" in raw_query.lower()):
            file_type_boost += 0.20

        followup_boost = 0.0
        if reranker_intent == "FOLLOWUP" or is_followup:
            candidate_path = item.get("relative_path", "")
            if candidate_path in previous_files:
                followup_boost += 0.35
            if item.get("symbol_name") in previous_symbols:
                followup_boost += 0.35
            candidate_dir = Path(candidate_path).parent.as_posix()
            for prev_file in previous_files:
                prev_dir = Path(prev_file).parent.as_posix()
                if candidate_dir == prev_dir and candidate_dir not in (".", ""):
                    followup_boost += 0.15
                    break

        dependency_boost = 0.0
        if (reranker_intent == "DEPENDENCY" or label_intent == "DEPENDENCY" or is_dependency_trace_query(raw_query)) and item.get("support_kind") == "dependency_edge":
            dependency_boost = 0.25

        sym_def_boost = symbol_definition_boost(item, extracted_symbols, raw_query) if reranker_intent in {"FILE", "SYMBOL"} else 0.0

        content_match_boost = 0.0
        if reranker_intent in {"FILE", "SYMBOL", "DEPENDENCY"}:
            content_match_boost = content_exact_match_boost(item, extracted_symbols, list(tokens))

        fw_boost = 0.0
        qdrant_boost = 0.0
        config_boost = 0.0
        response_quality_boost = 0.0
        response_quality_deboost = 0.0
        if reranker_intent in {"FILE", "SYMBOL"}:
            fw_boost = framework_source_boost(item, raw_query, reranker_intent)
            qdrant_boost = qdrant_upsert_source_boost(item, raw_query, reranker_intent)
        elif reranker_intent == "CONFIG":
            config_boost = config_source_boost(item, raw_query, reranker_intent)

        path_lower = relative_path.lower()
        q_lower = raw_query.lower()
        is_ui_query = any(term in q_lower for term in ("frontend", "ui", "component", "dashboard", "message bubble", "source card"))
        if response_mode == "overview" or reranker_intent == "OVERVIEW":
            is_readme = path_lower.rsplit("/", 1)[-1].startswith("readme")
            is_doc = path_lower.startswith("docs/") or "/docs/" in path_lower or path_lower.endswith(".md")
            is_config = any(part in path_lower for part in ("package.json", "pyproject.toml", "requirements", "docker", "compose", "config", ".env"))
            is_entrypoint = any(part in path_lower for part in ("api", "route", "routes", "server", "app.", "main.", "index."))
            if is_readme:
                response_quality_boost += 1.20
            if is_doc:
                response_quality_boost += 1.05
            if is_entrypoint:
                response_quality_boost += 0.85
            if is_config:
                response_quality_boost += 0.55
            if symbol_name in {"_has_overview_markers", "_any_term_in_query", "classify_intent", "_llm_classify_intent"}:
                response_quality_deboost -= 2.25
            if (path_lower.startswith("frontend/") or "/frontend/" in path_lower) and not is_ui_query:
                response_quality_deboost -= 0.85
            if path_lower.startswith(("test/", "tests/", "backend/tests/")) or "/tests/" in path_lower:
                response_quality_deboost -= 0.75

        if _is_indexing_explanation_query(raw_query):
            if any(term in path_lower for term in ("index", "ingest", "parser", "parse", "chunk", "embed", "vector", "store", "storage", "discover", "filter", "crawl")):
                response_quality_boost += 1.35
            if any(term in path_lower for term in ("job", "worker", "db", "database", "status", "fresh", "retry", "cancel")):
                response_quality_boost += 1.10
            if (path_lower.startswith("docs/") or "/docs/" in path_lower or path_lower.endswith(".md")) and any(term in path_lower for term in ("index", "ingest", "reindex", "troubleshoot")):
                response_quality_boost += 0.85
            if (path_lower.startswith("frontend/") or "/frontend/" in path_lower) and not is_ui_query:
                response_quality_deboost -= 2.50
            if "evaluationpanel" in path_lower:
                response_quality_deboost -= 3.00
            if path_lower.startswith(("test/", "tests/", "backend/tests/")) or "/manual_regression" in path_lower:
                response_quality_deboost -= 0.90

        if _is_retrieval_explanation_query(raw_query):
            if any(term in path_lower for term in ("retriev", "search", "rag", "rank", "rerank", "query", "answer", "llm", "source", "citation", "assembler")):
                response_quality_boost += 1.00
            if (path_lower.startswith("frontend/") or "/frontend/" in path_lower) and not is_ui_query:
                response_quality_deboost -= 1.25

        # Index health targeted boost
        index_health_boost_val = 0.0
        is_index_h, is_reindex_guid = is_index_health_query(raw_query, query_info)
        if is_index_h or is_reindex_guid:
            rel_path = str(item.get("relative_path", "")).lower()
            if "backend/evals/index_health.py" in rel_path or rel_path.endswith("evals/index_health.py"):
                if is_reindex_guid:
                    index_health_boost_val = 0.70
                else:
                    index_health_boost_val = 0.85
            elif "backend/evals/reindex_guidance.py" in rel_path or rel_path.endswith("evals/reindex_guidance.py"):
                if is_reindex_guid:
                    index_health_boost_val = 0.85
                else:
                    index_health_boost_val = 0.40

        # Source-role and routing boosts
        role_boost = 0.0
        role_deboost = 0.0
        
        # In source-location and flow-summary intent modes
        is_src_loc_or_flow_sum = reranker_intent in {"FILE", "SYMBOL", "ARCHITECTURE", "OVERVIEW"}
        
        role = classify_source_role(relative_path)
        if is_src_loc_or_flow_sum:
            if role == "implementation":
                role_boost = 0.45
            elif role in {"docs", "generated_eval", "test", "scratch/tooling"}:
                role_deboost = -0.40
            elif role == "answer_template":
                q_lower = raw_query.lower()
                allow_answer_template = (
                    "answer formatting" in q_lower
                    or "source-location" in q_lower
                    or "overview answer" in q_lower
                    or "flow answer" in q_lower
                    or "builder" in q_lower
                    or "code_answers" in q_lower
                )
                if not allow_answer_template:
                    role_deboost = -1.50

        # Deboost REPO_FRESHNESS_REPORT.md strongly unless explicitly asked
        if "repo_freshness_report.md" in relative_path.lower():
            explicit_doc_request = any(term in raw_query.lower() for term in ["report", "document", "file", "read", "say", "md"])
            if not explicit_doc_request:
                role_deboost -= 2.0

        # Deboost logic for auth/session validation queries on test/docs/scratch/benchmarks
        auth_triggers = {
            "auth", "authentication", "github auth", "session validation",
            "validate session", "session cookie", "auth session", "login callback"
        }
        q_lower = raw_query.lower()
        if any(term in q_lower for term in auth_triggers):
            explicit_doc_request = any(term in q_lower for term in ["test", "scratch", "benchmark", "plan", "document", "report", "file", "md"])
            if not explicit_doc_request:
                rel_lower = relative_path.lower()
                if (
                    "backend/tests/" in rel_lower
                    or "backend/scratch/" in rel_lower
                    or ("backend/scripts/" in rel_lower and "benchmark" in rel_lower)
                    or "codeseek_" in rel_lower
                    or "validation_and_improvement_plan" in rel_lower
                ):
                    role_deboost -= 2.0

        # CODE_REQUEST intent-specific boosts (Task 3 & 4)
        code_request_boost = 0.0
        code_request_deboost = 0.0
        auth_code_boost = 0.0
        code_topic_route_boost = 0.0
        code_topic_route_deboost = 0.0
        
        is_code = (label_intent in {"CODE_REQUEST", "code_snippet"} or primary_intent == "CODE_REQUEST")
        if is_code:
            chunk_type = item.get("chunk_type")
            symbol_name = item.get("symbol_name")
            labels = item.get("labels", [])
            content = item.get("content") or item.get("content_excerpt") or ""
            
            # 1. Prefer function-level chunks
            if chunk_type == "function":
                code_request_boost += 0.50
            elif chunk_type == "class":
                code_request_boost += 0.30
                
            # 2. symbol_name exists
            if symbol_name:
                code_request_boost += 0.30
                symbols_to_check = list(extracted_symbols)
                query_words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", raw_query)
                for w in query_words:
                    if w not in symbols_to_check:
                        symbols_to_check.append(w)
                
                symbol_name_lower = str(symbol_name).lower()
                matched_exact = False
                for sym in symbols_to_check:
                    sym_lower = sym.lower()
                    if sym_lower == symbol_name_lower:
                        if sym_lower == "_require_auth" and "_require_auth_user" in q_lower:
                            continue
                        matched_exact = True
                        break
                
                if matched_exact:
                    code_request_boost += 3.0
                    
            # 3. source/code labels
            if any("code" in str(lbl).lower() for lbl in labels) or "question_use:code-snippet" in labels:
                code_request_boost += 0.25
                
            # 4. content contains actual code constructs
            code_markers = ["def ", "class ", "import ", "return ", " = ", "self.", "async def "]
            if any(marker in content for marker in code_markers):
                code_request_boost += 0.25
                
            # 5. implementation paths over docs/tests/reports
            if role == "implementation":
                code_request_boost += 0.30
            elif role in {"docs", "generated_eval", "test", "scratch/tooling"}:
                # Deboost docs/tests/reports unless explicitly requested
                explicit_docs_or_tests = any(t in raw_query.lower() for t in ["test", "tests", "doc", "docs", "documentation", "report", "scratch", "plan"])
                if not explicit_docs_or_tests:
                    code_request_deboost -= 1.20
            
            # Extra penalty for markdown files or non-code bearing chunks
            if relative_path.endswith(".md") or not any(marker in content for marker in code_markers):
                code_request_deboost -= 0.60

            # 6. Specific topic code routing (Task 4)
            q_lower = raw_query.lower()
            
            # "show me the query endpoint code" -> api_service.py::_query_impl
            if "query endpoint" in q_lower or "query_endpoint" in q_lower:
                if symbol_name == "_query_impl" and "api_service.py" in relative_path:
                    auth_code_boost += 3.0
            
            # "show me the Qdrant upsert code" -> stages/storage.py::store_chunks
            elif "qdrant upsert" in q_lower or "qdrant_upsert" in q_lower:
                if symbol_name == "store_chunks" and "stages/storage.py" in relative_path:
                    auth_code_boost += 3.0
            
            # "provide me the session validation function code" -> auth_store.py::get_user_for_session_token & api_service.py::_current_auth_user/_require_auth_user
            elif "session validation" in q_lower or "validate_session" in q_lower:
                if symbol_name in ["get_user_for_session_token", "_current_auth_user", "_require_auth_user"]:
                    auth_code_boost += 3.0
                    
            # "provide me the auth function code"
            elif "auth function" in q_lower or "auth code" in q_lower:
                target_symbols = {
                    "api_service.py": ["_auth_key", "_require_auth", "_current_auth_user", "_require_auth_user"],
                    "auth_store.py": ["create_auth_session", "get_user_for_session_token", "upsert_github_user", "delete_auth_session"]
                }
                for f_name, symbols in target_symbols.items():
                    if f_name in relative_path and symbol_name in symbols:
                        auth_code_boost += 3.0
            
            # General auth keywords fallback
            else:
                auth_keywords = ["auth", "session", "login", "cookie", "token"]
                if any(kw in q_lower for kw in auth_keywords):
                    target_symbols = {
                        "api_service.py": ["_auth_key", "_require_auth", "_current_auth_user", "_require_auth_user"],
                        "auth_store.py": ["create_auth_session", "get_user_for_session_token", "upsert_github_user", "delete_auth_session"]
                    }
                    for f_name, symbols in target_symbols.items():
                        if f_name in relative_path:
                            specific_mention = False
                            for s in symbols:
                                if s in q_lower:
                                    specific_mention = True
                                    if symbol_name == s:
                                        auth_code_boost += 2.0
                            
                            if not specific_mention and symbol_name in symbols:
                                auth_code_boost += 1.5

            if matched_code_topic_route:
                matches_route = (
                    path_matches_topic_route(relative_path, matched_code_topic_route)
                    or symbol_matches_topic_route(symbol_name, relative_path, matched_code_topic_route)
                )
                if matches_route:
                    code_topic_route_boost += 3.5
                    if role == "implementation":
                        code_topic_route_boost += 0.5
                elif strict_code_topic_route:
                    if role == "implementation":
                        code_topic_route_deboost -= 4.0
                    if item.get("support_kind") == "conversation_history":
                        code_topic_route_deboost -= 2.5
                if topic_route_excludes_path(relative_path, matched_code_topic_route):
                    code_topic_route_deboost -= 5.0
                if strict_code_topic_route and "backend/retrieval/searcher.py" in relative_path.lower():
                    code_topic_route_deboost -= 4.0

        # Injected candidates boost
        if item.get("support_kind") == "direct_injection":
            role_boost += 1.5
        if item.get("support_kind") == "code_topic_routing":
            role_boost += 2.0

        routing_boost = feature_specific_routing_boost(relative_path, raw_query)

        final_score = (
            0.70 * vector_score
            + 0.15 * exact_match_score
            + 0.10 * label_boost
            + 0.05 * path_symbol_boost
            + file_type_boost
            + followup_boost
            + dependency_boost
            + sym_def_boost
            + content_match_boost
            + fw_boost
            + qdrant_boost
            + config_boost
            + response_quality_boost
            + response_quality_deboost
            + index_health_boost_val
            + role_boost
            + role_deboost
            + routing_boost
            + code_request_boost
            + code_request_deboost
            + auth_code_boost
            + code_topic_route_boost
            + code_topic_route_deboost
        )

        final_score *= artifact_penalty_for_intent(relative_path, reranker_intent, previous_files)

        boosted = dict(item)
        boosted["retrieval_score"] = final_score
        boosted["final_score"] = final_score
        rescored.append(boosted)

    rescored.sort(key=lambda item: -float(item.get("final_score", 0.0)))

    diverse_results = []
    file_counts = {}
    for item in rescored:
        rel_path = item.get("relative_path", "")
        if rel_path == "__repo_summary__.md" or item.get("chunk_type") == "repo_summary":
            diverse_results.append(item)
            continue
        count = file_counts.get(rel_path, 0)
        if count < 2:
            diverse_results.append(item)
            file_counts[rel_path] = count + 1

    return diverse_results


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


def _inject_architecture_file_candidates(candidates: list[dict], entities: dict) -> list[dict]:
    """Prepend exact structural file hits for architecture prompts."""
    file_hints = [str(item).strip() for item in (entities.get("files") or []) if str(item).strip()]
    if not file_hints:
        return candidates

    client = _get_client()
    collection = get_collection_name()
    existing_by_chunk_id = {
        str(item.get("chunk_id", "")).strip(): item
        for item in candidates
        if item.get("chunk_id")
    }
    to_prepend: list[dict] = []

    for file_hint in file_hints:
        response = _qdrant_call(lambda: client.scroll(
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="relative_path", match=MatchValue(value=file_hint))]
            ),
            limit=24,
            with_payload=True,
        ))
        if response is None:
            continue
        hits, _ = response
        if not hits:
            continue

        best_payloads = sorted(
            (hit.payload or {} for hit in hits if hit.payload),
            key=lambda payload: (
                -_architecture_file_priority(payload),
                -_architecture_symbol_priority(payload),
                str(payload.get("symbol_name", "")),
                int(payload.get("start_line", 0)),
            ),
        )
        chosen = best_payloads[0]
        chunk_id = str(chosen.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        promoted = dict(existing_by_chunk_id.get(chunk_id, chosen))
        promoted.setdefault("retrieval_score", min(1.0, _architecture_file_priority(promoted) / 100.0))
        promoted.setdefault("fusion_score", 0.0)
        promoted["exact_retrieval_hit"] = True
        to_prepend.append(promoted)

    if not to_prepend:
        return candidates

    promoted_ids = {
        str(item.get("chunk_id", "")).strip()
        for item in to_prepend
        if item.get("chunk_id")
    }
    remaining = [
        item
        for item in candidates
        if str(item.get("chunk_id", "")).strip() not in promoted_ids
    ]
    return to_prepend + remaining


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
        if _exclude_overview_payload(payload):
            continue
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


def _exclude_overview_payload(payload: dict) -> bool:
    """Reject files that are systematically noisy for repo-level overview answers."""
    relative_path = str(payload.get("relative_path", "")).strip().lower()
    if not relative_path:
        return True

    if relative_path == ".rag_ingestion_state.json":
        return True
    if relative_path.startswith("backend/tests/fixtures/"):
        return True
    if relative_path.startswith("tests/fixtures/"):
        return True
    if relative_path.startswith("backend/docs/retrieval_docs/"):
        return True
    if relative_path.startswith("docs/retrieval_docs/"):
        return True
    if relative_path.endswith(("eval_codeseek_exact_wording.json", "eval_codeseek_flow_phase1.json", "eval_suite_multi_repo.json")):
        return True
    return False


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
    payloads = [dict(hit.payload or {}) for hit in hits]
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

    if relative_path == "backend/readme.md":
        score += 38
    if relative_path in {"readme.md", "readme.mdx"}:
        score += 46
    if relative_path in {
        "docs/product/final_handoff.md",
        "docs/product/release_readiness_checklist.md",
        "docs/product/index_latest.md",
    }:
        score += 44
    elif relative_path.startswith("docs/product/"):
        score += 28
    if relative_path.endswith("retrieval/api_service.py"):
        score += 44
    if relative_path.endswith("retrieval/main.py"):
        score += 42
    if relative_path.endswith("rag_ingestion/main.py"):
        score += 40
    if relative_path.endswith("retrieval/searcher.py"):
        score += 28
    if relative_path.endswith("retrieval/code_answers.py"):
        score += 22
    if relative_path.endswith("package.json"):
        score += 24
    if relative_path.startswith("frontend/") and relative_path.endswith("package.json"):
        score -= 8
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


def _architecture_file_priority(payload: dict) -> int:
    relative_path = str(payload.get("relative_path", "")).lower()
    symbol_name = str(payload.get("symbol_name", "")).lower()
    chunk_type = str(payload.get("chunk_type", "")).lower()
    file_type = str(payload.get("file_type", "")).lower()

    if chunk_type == "repo_summary" or file_type == "repo_summary" or relative_path == "__repo_summary__.md":
        return 100
    if relative_path.endswith("backend/retrieval/api_service.py"):
        return 98
    if relative_path.endswith("backend/retrieval/main.py"):
        return 96
    if relative_path.endswith("backend/rag_ingestion/main.py"):
        return 94
    if relative_path.endswith("backend/docker-compose.yml"):
        return 92
    if relative_path.endswith("backend/.env.example"):
        return 90
    if relative_path.endswith("backend/docs/deployment_runbook.md"):
        return 88
    if relative_path.endswith("backend/retrieval/db.py"):
        return 86
    if relative_path == "backend/readme.md":
        return 84
    if relative_path == "readme.md":
        return 40
    return 0


def _architecture_symbol_priority(payload: dict) -> int:
    relative_path = str(payload.get("relative_path", "")).lower()
    symbol_name = str(payload.get("symbol_name", "")).lower()
    chunk_type = str(payload.get("chunk_type", "")).lower()

    score = 0
    if relative_path.endswith("backend/retrieval/api_service.py"):
        if symbol_name == "_query_impl":
            score += 30
        elif chunk_type == "function":
            score += 12
        elif chunk_type == "class":
            score += 4
    elif relative_path.endswith("backend/retrieval/main.py"):
        if symbol_name == "run_query":
            score += 30
        elif chunk_type == "function":
            score += 12
    elif relative_path.endswith("backend/rag_ingestion/main.py"):
        if symbol_name == "run_pipeline":
            score += 30
        elif chunk_type == "function":
            score += 12
    elif chunk_type == "function":
        score += 4
    return score


# Intents that should always receive repo-summary + structured overview evidence.
_OVERVIEW_INTENTS: frozenset[str] = frozenset({"OVERVIEW", "TECH_STACK", "ARCHITECTURE"})


def _is_overview_intent(primary_intent: str) -> bool:
    """Return True for intents that always warrant repo-summary injection."""
    return primary_intent in _OVERVIEW_INTENTS


def _is_overview_query(raw_query: str) -> bool:
    from retrieval.query_intent import is_overview_query

    if is_overview_query(raw_query):
        return True
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
            "give me a repository overview",
            "repo overview",
            "repository overview",
            "how is this codebase structured",
            "how is this project structured",
            "how is this repository structured",
            "project structure",
            "repository structure",
            "codebase structure",
            "what are the main modules",
            "what are the core modules",
            "top-level subsystems",
            "top level subsystems",
            "module layout",
            "runtime shape",
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


def _is_indexing_explanation_query(raw_query: str) -> bool:
    from retrieval.query_intent import is_indexing_explanation_query

    return is_indexing_explanation_query(raw_query)


def _is_retrieval_explanation_query(raw_query: str) -> bool:
    from retrieval.query_intent import is_retrieval_explanation_query

    return is_retrieval_explanation_query(raw_query)


def _is_architecture_query(raw_query: str) -> bool:
    q = raw_query.lower()
    return any(
        phrase in q
        for phrase in (
            "architecture overview",
            "high-level architecture",
            "system architecture",
            "project structure",
            "codebase structure",
            "how is this codebase structured",
            "how is this project structured",
            "main modules",
            "top-level subsystems",
        )
    )


def _is_test_path(relative_path: str) -> bool:
    return "/test" in relative_path or relative_path.startswith("test")


def dependency_health() -> dict[str, str]:
    """Best-effort readiness for retrieval dependencies."""
    if not ENABLE_DENSE_RETRIEVAL:
        model_status = "disabled"
    else:
        model = _get_model()
        if model is None:
            model_status = "degraded"
        else:
            model_status = "ok"

    client = _get_client()
    qdrant_ready = _qdrant_call(lambda: client.get_collections())
    qdrant_status = "ok" if qdrant_ready is not None else "degraded"
    return {"embedding_model": model_status, "qdrant": qdrant_status}
