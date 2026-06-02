"""Authenticated, rate-limited HTTP wrapper for retrieval pipeline."""

from __future__ import annotations

import os
import time
import threading
import uuid
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from retrieval.chat_store import (
    append_message,
    clear_session_messages,
    list_session_messages,
)
from retrieval.config import get_collection_name, get_repo_root
from retrieval.db import init_db
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
from retrieval.session_indexer import (
    create_session,
    delete_session,
    get_session,
    list_sessions,
    retry_indexing,
)

RATE_LIMIT_PER_MINUTE = int(os.getenv("CODESEEK_RATE_LIMIT_PER_MINUTE", "60"))
API_KEY_ENV = "CODESEEK_API_KEY"
DEFAULT_TENANT_ID = os.getenv("CODESEEK_TENANT_ID", "local")
STRICT_ENV_VALIDATION = os.getenv("CODESEEK_STRICT_ENV_VALIDATION", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_USER_URL = "https://api.github.com/user"

app = FastAPI(title="Codeseek Retrieval API", version="1.0.0")
v1 = APIRouter(prefix="/api/v1", tags=["v1"])
memory = ConversationMemory(max_turns=5)
_request_windows: dict[str, deque[float]] = defaultdict(deque)
_startup_errors: list[str] = []
_query_lock = threading.Lock()
_provider_configs_lock = threading.Lock()
_provider_configs: dict[str, dict[str, str]] = {}


def _cors_origins() -> list[str]:
    raw = os.getenv("CODESEEK_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    query: str | None = None
    question: str | None = None
    session_id: str | None = None
    provider_config_id: str | None = None


class ProviderConfigRegisterRequest(BaseModel):
    provider: str
    api_key: str
    model: str | None = None
    label: str | None = None


class SessionCreateRequest(BaseModel):
    repo_full_name: str
    repo_url: str | None = None
    tenant_id: str | None = None
    github_token: str | None = None


class GithubAuthCodeRequest(BaseModel):
    code: str


def _ready_sessions() -> list[dict]:
    return [session for session in list_sessions() if session.get("status") == "ready"]


def _register_provider_config(
    provider: str,
    api_key: str,
    model: str = "",
    label: str = "",
) -> dict[str, str]:
    config_id = uuid.uuid4().hex
    record = {
        "id": config_id,
        "provider": provider.strip().lower(),
        "api_key": api_key.strip(),
        "model": model.strip(),
        "label": label.strip(),
        "created_at": str(int(time.time())),
    }
    with _provider_configs_lock:
        _provider_configs[config_id] = record
    return {
        "id": record["id"],
        "provider": record["provider"],
        "model": record["model"],
        "label": record["label"],
        "created_at": record["created_at"],
    }


def _get_provider_config(config_id: str) -> dict[str, str] | None:
    with _provider_configs_lock:
        record = _provider_configs.get(config_id)
        if not record:
            return None
        return dict(record)


def _resolve_query_session(session_id: str | None) -> dict | None:
    if session_id:
        session = get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"Session is not ready (status={session.get('status')})",
            )
        return session

    ready_sessions = _ready_sessions()
    if len(ready_sessions) == 1:
        return ready_sessions[0]
    return None


@app.on_event("startup")
def startup_checks() -> None:
    _startup_errors.clear()
    init_db()
    missing = []
    if not os.getenv(API_KEY_ENV, "").strip():
        missing.append(API_KEY_ENV)
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


def _github_oauth_config() -> tuple[str, str, str]:
    client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="GitHub OAuth is not configured on server (missing GITHUB_CLIENT_ID or GITHUB_CLIENT_SECRET)",
        )
    return client_id, client_secret, redirect_uri


def _exchange_github_code(code: str) -> dict:
    client_id, client_secret, redirect_uri = _github_oauth_config()
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code.strip(),
    }
    if redirect_uri:
        payload["redirect_uri"] = redirect_uri
    response = httpx.post(
        GITHUB_OAUTH_TOKEN_URL,
        headers={"Accept": "application/json"},
        json=payload,
        timeout=15.0,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        description = data.get("error_description") or data.get("error")
        raise HTTPException(status_code=400, detail=f"GitHub OAuth exchange failed: {description}")
    access_token = str(data.get("access_token", "")).strip()
    if not access_token:
        raise HTTPException(status_code=502, detail="GitHub OAuth exchange did not return an access token")
    return data


def _fetch_github_user(access_token: str) -> dict:
    response = httpx.get(
        GITHUB_API_USER_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


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
    token = _require_auth(authorization)
    client_ip = request.client.host if request.client else "unknown"
    _enforce_rate_limit(f"{token}:{client_ip}")
    query_text = (body.query or body.question or "").strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="Missing query text (use query or question)")
    provider_config_id = (body.provider_config_id or "").strip()
    if not provider_config_id:
        raise HTTPException(
            status_code=400,
            detail="Active frontend LLM provider config is required (provider_config_id)",
        )
    provider_config = _get_provider_config(provider_config_id)
    if not provider_config:
        raise HTTPException(
            status_code=400,
            detail="provider_config_id is invalid or expired; re-register the active provider config",
        )
    previous_repo = os.getenv("RETRIEVAL_REPO_ROOT", "")
    previous_collection = os.getenv("QDRANT_COLLECTION_NAME", "")
    session = _resolve_query_session(body.session_id)
    if session:
        os.environ["RETRIEVAL_REPO_ROOT"] = session["repo_root"]
        os.environ["QDRANT_COLLECTION_NAME"] = session["collection"]
        log_event(
            "api.query.session_bound",
            request_id,
            session_id=session.get("id"),
            repo_root=session.get("repo_root"),
            collection=session.get("collection"),
        )
    try:
        validate_collection_binding(get_collection_name(), get_repo_root())
    except ValueError as exc:
        total_ms = int((time.perf_counter() - started) * 1000)
        observe_api_request(path, "409", total_ms)
        RETRIEVAL_ERRORS_TOTAL.labels(error_type="isolation").inc()
        log_event("api.query.error", request_id, error=str(exc), status_code=409)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        with _query_lock:
            answer, sources, token_count, meta = run_query(
                query_text,
                memory,
                request_id=request_id,
                return_meta=True,
                provider_config=provider_config,
            )
        if session:
            append_message(session["id"], "user", query_text)
            append_message(
                session["id"],
                "assistant",
                answer,
                sources=sources,
                context_tokens=token_count,
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
    finally:
        if session:
            if previous_repo:
                os.environ["RETRIEVAL_REPO_ROOT"] = previous_repo
            else:
                os.environ.pop("RETRIEVAL_REPO_ROOT", None)
            if previous_collection:
                os.environ["QDRANT_COLLECTION_NAME"] = previous_collection
            else:
                os.environ.pop("QDRANT_COLLECTION_NAME", None)


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


@v1.post("/provider-configs")
def register_provider_config_v1(
    body: ProviderConfigRegisterRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    provider = body.provider.strip().lower()
    api_key = body.api_key.strip()
    model = (body.model or "").strip()
    label = (body.label or "").strip()
    if not provider or not api_key:
        raise HTTPException(status_code=400, detail="provider and api_key are required")
    record = _register_provider_config(provider, api_key, model=model, label=label)
    return {"provider_config": record}


@v1.post("/sessions")
def create_session_v1(
    body: SessionCreateRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    tenant_id = (body.tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    try:
        session = create_session(
            repo_full_name=body.repo_full_name.strip(),
            tenant_id=tenant_id,
            repo_url=(body.repo_url or "").strip(),
            github_token=(body.github_token or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": session}


@v1.get("/sessions")
def list_sessions_v1(authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    return {"sessions": list_sessions()}


@v1.get("/sessions/{session_id}")
def get_session_v1(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session}


@v1.get("/sessions/{session_id}/messages")
def list_session_messages_v1(
    session_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": list_session_messages(session_id)}


@v1.delete("/sessions/{session_id}/messages")
def clear_session_messages_v1(
    session_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_auth(authorization)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    deleted = clear_session_messages(session_id)
    return {"cleared": deleted, "session_id": session_id}


@v1.delete("/sessions/{session_id}")
def delete_session_v1(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    deleted = delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True, "session_id": session_id}


@v1.post("/sessions/{session_id}/retry")
def retry_session_v1(session_id: str, authorization: str | None = Header(default=None)) -> dict:
    _require_auth(authorization)
    session = retry_indexing(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session}


@app.post("/auth/github")
def auth_github(body: GithubAuthCodeRequest) -> dict:
    code = body.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    try:
        token_data = _exchange_github_code(code)
        access_token = str(token_data.get("access_token", "")).strip()
        user = _fetch_github_user(access_token)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or str(exc)
        raise HTTPException(status_code=502, detail=f"GitHub OAuth request failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub OAuth network error: {exc}") from exc

    return {
        "access_token": access_token,
        "username": str(user.get("login", "")).strip(),
        "avatar_url": str(user.get("avatar_url", "")).strip(),
    }


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
