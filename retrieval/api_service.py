"""Authenticated, rate-limited HTTP wrapper for retrieval pipeline."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from retrieval.config import GROQ_API_KEY_ENV, get_collection_name, get_repo_root
from retrieval.isolation import validate_collection_binding
from retrieval.main import run_query
from retrieval.memory import ConversationMemory
from retrieval.observability import (
    RETRIEVAL_ERRORS_TOTAL,
    log_event,
    new_request_id,
    observe_api_request,
    observe_retrieval_meta,
    render_prometheus_metrics,
)
from retrieval.searcher import dependency_health

RATE_LIMIT_PER_MINUTE = int(os.getenv("CODESEEK_RATE_LIMIT_PER_MINUTE", "60"))
API_KEY_ENV = "CODESEEK_API_KEY"
STRICT_ENV_VALIDATION = os.getenv("CODESEEK_STRICT_ENV_VALIDATION", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

app = FastAPI(title="Codeseek Retrieval API", version="1.0.0")
v1 = APIRouter(prefix="/api/v1", tags=["v1"])
memory = ConversationMemory(max_turns=5)
_request_windows: dict[str, deque[float]] = defaultdict(deque)
_startup_errors: list[str] = []


class QueryRequest(BaseModel):
    query: str


@app.on_event("startup")
def startup_checks() -> None:
    _startup_errors.clear()
    missing = []
    if not os.getenv(API_KEY_ENV, "").strip():
        missing.append(API_KEY_ENV)
    if not os.getenv(GROQ_API_KEY_ENV, "").strip():
        missing.append(GROQ_API_KEY_ENV)
    if missing:
        _startup_errors.append(f"missing required env: {', '.join(missing)}")
    if not os.path.isdir(get_repo_root()):
        _startup_errors.append(f"repo root not found: {get_repo_root()}")
    if RATE_LIMIT_PER_MINUTE <= 0:
        _startup_errors.append("invalid CODESEEK_RATE_LIMIT_PER_MINUTE (must be > 0)")
    try:
        validate_collection_binding(get_collection_name(), get_repo_root())
    except ValueError as exc:
        _startup_errors.append(str(exc))
    # Probe retrieval dependencies once at startup.
    dep = dependency_health()
    if dep.get("qdrant") != "ok":
        _startup_errors.append(
            f"qdrant unavailable for collection {get_collection_name()}"
        )
    if STRICT_ENV_VALIDATION and _startup_errors:
        raise RuntimeError("Startup validation failed: " + "; ".join(_startup_errors))


def _auth_key(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Expected Bearer token")
    return authorization.split(" ", 1)[1].strip()


def _require_auth(authorization: str | None) -> str:
    expected = os.getenv(API_KEY_ENV, "").strip()
    if not expected:
        raise HTTPException(
            status_code=500, detail=f"{API_KEY_ENV} is not configured on server"
        )
    token = _auth_key(authorization)
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


def _enforce_rate_limit(bucket_key: str) -> None:
    now = time.time()
    window = _request_windows[bucket_key]
    cutoff = now - 60.0
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    window.append(now)


def _health_payload() -> dict[str, str]:
    dep = dependency_health()
    if _startup_errors or dep.get("qdrant") != "ok":
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "collection": get_collection_name(),
        "repo_root": get_repo_root(),
        "embedding_model": dep.get("embedding_model", "unknown"),
        "qdrant": dep.get("qdrant", "unknown"),
        "startup_errors": "; ".join(_startup_errors) if _startup_errors else "",
    }


def _query_impl(
    body: QueryRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> dict:
    request_id = x_request_id or new_request_id()
    started = time.perf_counter()
    path = "/api/v1/query"
    log_event("api.query.start", request_id, path="/query")
    try:
        validate_collection_binding(get_collection_name(), get_repo_root())
    except ValueError as exc:
        total_ms = int((time.perf_counter() - started) * 1000)
        observe_api_request(path, "409", total_ms)
        RETRIEVAL_ERRORS_TOTAL.labels(error_type="isolation").inc()
        log_event("api.query.error", request_id, error=str(exc), status_code=409)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token = _require_auth(authorization)
    client_ip = request.client.host if request.client else "unknown"
    _enforce_rate_limit(f"{token}:{client_ip}")
    try:
        answer, sources, token_count, meta = run_query(
            body.query, memory, request_id=request_id, return_meta=True
        )
        total_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            "api.query.end",
            request_id,
            status="ok",
            total_latency_ms=total_ms,
            context_tokens=token_count,
            source_count=len(sources),
            stage_latency_ms=meta.get("stage_latency_ms", {}),
        )
        observe_api_request(path, "200", total_ms)
        observe_retrieval_meta(meta, source_count=len(sources), context_tokens=token_count)
        return {
            "request_id": request_id,
            "answer": answer,
            "sources": sources,
            "context_tokens": token_count,
            "metrics": {
                "total_latency_ms": total_ms,
                "stage_latency_ms": meta.get("stage_latency_ms", {}),
                "source_filter": meta.get("source_filter", {}),
            },
        }
    except HTTPException:
        total_ms = int((time.perf_counter() - started) * 1000)
        observe_api_request(path, "error", total_ms)
        RETRIEVAL_ERRORS_TOTAL.labels(error_type="http_exception").inc()
        raise
    except Exception as exc:
        total_ms = int((time.perf_counter() - started) * 1000)
        observe_api_request(path, "500", total_ms)
        RETRIEVAL_ERRORS_TOTAL.labels(error_type="internal").inc()
        log_event(
            "api.query.error",
            request_id,
            error=str(exc),
            status_code=500,
            total_latency_ms=total_ms,
        )
        raise HTTPException(status_code=500, detail="Internal retrieval error") from exc


@v1.get("/health")
def health_v1() -> dict[str, str]:
    return _health_payload()


@v1.get("/metrics")
def metrics_v1() -> Response:
    body, content_type = render_prometheus_metrics()
    return Response(content=body, media_type=content_type)


@v1.post("/query")
def query_v1(
    body: QueryRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> dict:
    return _query_impl(body, request, authorization, x_request_id)


# Backward-compatible aliases
@app.get("/health")
def health() -> dict[str, str]:
    return _health_payload()


@app.get("/metrics")
def metrics() -> Response:
    body, content_type = render_prometheus_metrics()
    return Response(content=body, media_type=content_type)


@app.post("/query")
def query(
    body: QueryRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> dict:
    return _query_impl(body, request, authorization, x_request_id)


app.include_router(v1)
