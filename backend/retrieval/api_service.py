"""Authenticated, rate-limited HTTP wrapper for retrieval pipeline."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import threading
import urllib.parse
import uuid
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, Cookie, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from retrieval.auth_store import (
    create_auth_session,
    delete_auth_session,
    get_user_for_session_token,
    upsert_github_user,
)
from retrieval.chat_store import (
    append_message,
    append_thread_message,
    clear_session_messages,
    clear_thread_messages,
    list_session_messages,
    list_thread_messages,
)
from retrieval.config import get_collection_name, get_repo_root
from retrieval.crypto_store import has_explicit_app_encryption_key
from retrieval.db import init_db
from retrieval.github_store import get_github_credential, upsert_github_credential
from retrieval.isolation import validate_collection_binding
from retrieval.main import run_query
from retrieval.memory import ConversationMemory, SessionConversationMemory, ThreadConversationMemory
from retrieval.llm import LlmProviderError
from retrieval.observability import (
    RETRIEVAL_ERRORS_TOTAL,
    log_event,
    new_request_id,
    observe_api_request,
    observe_retrieval_meta,
    render_prometheus_metrics,
    sanitize_for_log,
)
from retrieval.provider_store import (
    SUPPORTED_PROVIDER_TYPES,
    create_provider_credential,
    delete_provider_credential,
    get_active_provider_credential,
    list_provider_credentials,
    set_active_provider_credential,
)
from retrieval.local_llm_runtime import (
    background_prime_primary_model,
    get_provider_runtime_state,
)
from retrieval.provider_health import (
    ProviderNotConfiguredError,
    ProviderNotReadyError,
    require_llm_ready_for_user,
)
from retrieval.ragas_reports import load_ragas_validation_bundle
from retrieval.searcher import dependency_health
from retrieval.session_indexer import (
    create_session,
    delete_session,
    get_session,
    list_sessions,
    retry_indexing,
)
from retrieval.submission_crypto import (
    decrypt_submission_secret,
    get_submission_key_id,
    get_submission_public_key_pem,
)
from retrieval.thread_store import create_thread, ensure_default_thread, get_thread, list_threads_for_session
from retrieval.indexing_events import (
    get_indexing_events,
    subscribe_indexing_events,
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
AUTH_SESSION_COOKIE = os.getenv("CODESEEK_AUTH_SESSION_COOKIE", "codeseek_session")
AUTH_SESSION_SECURE_COOKIE = os.getenv("CODESEEK_AUTH_SESSION_SECURE_COOKIE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ENFORCE_HTTPS = os.getenv("CODESEEK_ENFORCE_HTTPS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TRUST_X_FORWARDED_PROTO = os.getenv("CODESEEK_TRUST_X_FORWARDED_PROTO", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ALLOW_PLAINTEXT_SECRET_SUBMISSION = os.getenv(
    "CODESEEK_ALLOW_PLAINTEXT_SECRET_SUBMISSION",
    "1",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REQUIRE_EXPLICIT_APP_ENCRYPTION_KEY = os.getenv(
    "CODESEEK_REQUIRE_EXPLICIT_APP_ENCRYPTION_KEY",
    "0",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_USER_URL = "https://api.github.com/user"
CODESEEK_FRONTEND_URL = os.getenv("CODESEEK_FRONTEND_URL", "http://localhost:5173")
OAUTH_STATE_COOKIE = "codeseek_oauth_state"

app = FastAPI(title="Codeseek Retrieval API", version="1.0.0")

import sqlite3

@app.exception_handler(sqlite3.OperationalError)
def sqlite_operational_error_handler(request: Request, exc: sqlite3.OperationalError):
    if "no such table" in str(exc).lower():
        try:
            from retrieval.db import init_db
            init_db(force=True)
        except Exception as init_exc:
            return JSONResponse(
                status_code=503,
                content={"detail": f"Database initialization failed: {init_exc}"},
            )
        return JSONResponse(
            status_code=503,
            content={"detail": "Backend database is initializing. Please retry in a moment."},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Database operational error: {exc}"},
    )

v1 = APIRouter(prefix="/api/v1", tags=["v1"])
memory = ConversationMemory(max_turns=5)
_request_windows: dict[str, deque[float]] = defaultdict(deque)
_startup_errors: list[str] = []
_query_lock = threading.Lock()

def _cors_origins() -> list[str]:
    raw = os.getenv("CODESEEK_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if os.getenv("CODESEEK_TENANT_ID", "local") == "local":
        local_dev_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://0.0.0.0:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
        for local_origin in local_dev_origins:
            if local_origin not in origins:
                origins.append(local_origin)
    return origins


def _cors_origin_regex() -> str | None:
    if os.getenv("CODESEEK_TENANT_ID", "local") == "local":
        return r"^http://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$"
    return None


def _is_https_request(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    if not TRUST_X_FORWARDED_PROTO:
        return False
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    return forwarded_proto.split(",", 1)[0].strip().lower() == "https"


def _allow_http_request(request: Request) -> bool:
    return request.url.path in {
        "/health",
        "/metrics",
        "/api/v1/health",
        "/api/v1/metrics",
    }


@app.middleware("http")
async def enforce_https_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    if ENFORCE_HTTPS and not _allow_http_request(request) and not _is_https_request(request):
        return JSONResponse(
            status_code=400,
            content={"detail": "HTTPS is required for this deployment"},
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=_cors_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from retrieval.crypto_store import master_key_override_var

@app.middleware("http")
async def app_encryption_key_middleware(request: Request, call_next):
    key = request.headers.get("x-app-encryption-key", "").strip()
    if key:
        token = master_key_override_var.set(key)
        try:
            return await call_next(request)
        finally:
            master_key_override_var.reset(token)
    return await call_next(request)


class QueryRequest(BaseModel):
    query: str | None = None
    question: str | None = None
    session_id: str | None = None
    thread_id: str | None = None


class ProviderCredentialCreateRequest(BaseModel):
    provider: str
    api_key: str | None = None
    encrypted_secret: dict | None = None
    model: str | None = None
    label: str | None = None
    is_active: bool | None = None


class SessionCreateRequest(BaseModel):
    repo_full_name: str
    repo_url: str | None = None
    tenant_id: str | None = None
    github_token: str | None = None
    enable_chunk_descriptions: bool = False


class SessionIndexingOptionsUpdateRequest(BaseModel):
    refine_labels_with_llm: bool


class GithubAuthCodeRequest(BaseModel):
    code: str


class GithubTokenConnectRequest(BaseModel):
    access_token: str | None = None
    encrypted_secret: dict | None = None


class SubmissionPublicKeyResponse(BaseModel):
    key_id: str
    algorithm: str
    public_key_pem: str


class RagasValidationArtifactResponse(BaseModel):
    artifacts: dict
    report: dict | None = None
    family_baseline: dict | None = None
    family_baseline_trend: dict | None = None
    human_review_benchmark: dict | None = None


def _ready_sessions() -> list[dict]:
    return [session for session in list_sessions() if session.get("status") == "ready"]


def _session_visible_to_user(session: dict, auth_user: dict | None) -> bool:
    owner_id = (session.get("user_id") or "").strip()
    if not owner_id:
        return True
    if not auth_user:
        return False
    return owner_id == auth_user["id"]


def _thread_visible_to_user(thread: dict, auth_user: dict | None) -> bool:
    owner_id = (thread.get("user_id") or "").strip()
    if not owner_id:
        return True
    if not auth_user:
        return False
    return owner_id == auth_user["id"]


def _resolve_query_session(session_id: str | None, auth_user: dict | None = None) -> dict | None:
    if session_id:
        session = get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not _session_visible_to_user(session, auth_user):
            raise HTTPException(status_code=404, detail="Session not found")
        if session.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"Session is not ready (status={session.get('status')})",
            )
        return session

    ready_sessions = [session for session in _ready_sessions() if _session_visible_to_user(session, auth_user)]
    if len(ready_sessions) == 1:
        return ready_sessions[0]
    return None


def _resolve_submitted_secret(plaintext: str | None, encrypted_secret: dict | None) -> str:
    raw = (plaintext or "").strip()
    if raw:
        if not ALLOW_PLAINTEXT_SECRET_SUBMISSION:
            raise HTTPException(
                status_code=400,
                detail="Plaintext secret submission is disabled; refresh and retry with encrypted submission",
            )
        return raw
    if not encrypted_secret:
        return ""
    key_id = str(encrypted_secret.get("key_id", "")).strip()
    ciphertext = str(encrypted_secret.get("ciphertext", "")).strip()
    if not key_id or not ciphertext:
        raise HTTPException(status_code=400, detail="Encrypted secret payload is incomplete")
    try:
        return decrypt_submission_secret(ciphertext, key_id=key_id).strip()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Encrypted secret could not be decrypted") from exc


def _enrich_provider_runtime(record: dict) -> dict:
    if not record:
        return record
    runtime = get_provider_runtime_state(record.get("provider", ""), record.get("model", ""))
    enriched = dict(record)
    enriched["runtime_status"] = runtime.get("status", "")
    enriched["runtime_detail"] = runtime.get("detail", "")
    enriched["runtime_selected_model"] = runtime.get("selected_model", "")
    enriched["runtime_selected_status"] = runtime.get("selected_status", "")
    enriched["runtime_selected_detail"] = runtime.get("selected_detail", "")
    enriched["runtime_primary_model"] = runtime.get("primary_model", "")
    enriched["runtime_primary_status"] = runtime.get("primary_status", "")
    enriched["runtime_primary_detail"] = runtime.get("primary_detail", "")
    return enriched


def _enrich_provider_runtime_list(records: list[dict]) -> list[dict]:
    return [_enrich_provider_runtime(record) for record in records]


@app.on_event("startup")
def startup_checks() -> None:
    tenant = os.getenv("CODESEEK_TENANT_ID", "local")
    print(f"[api.cors] tenant={tenant}")
    print(f"[api.cors] allowed_origins={_cors_origins()}")
    print(f"[api.cors] allow_origin_regex={_cors_origin_regex()}")
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
    if ENFORCE_HTTPS and not AUTH_SESSION_SECURE_COOKIE:
        _startup_errors.append(
            "CODESEEK_AUTH_SESSION_SECURE_COOKIE must be enabled when CODESEEK_ENFORCE_HTTPS=1"
        )
    if REQUIRE_EXPLICIT_APP_ENCRYPTION_KEY and not has_explicit_app_encryption_key():
        _startup_errors.append(
            "CODESEEK_APP_ENCRYPTION_KEY must be set explicitly in deployment"
        )
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


def _fetch_github_repos(access_token: str) -> list[dict]:
    page = 1
    repos: list[dict] = []
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    while True:
        response = httpx.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={
                "per_page": 100,
                "page": page,
                "sort": "updated",
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
            },
            timeout=20.0,
        )
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def _cookie_settings() -> dict[str, object]:
    return {
        "httponly": True,
        "secure": AUTH_SESSION_SECURE_COOKIE,
        "samesite": "lax",
        "path": "/",
    }


def _current_auth_user(session_token: str | None) -> dict | None:
    raw = (session_token or "").strip()
    if not raw:
        return None
    return get_user_for_session_token(raw)


def _require_auth_user(session_token: str | None) -> dict:
    user = _current_auth_user(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _persist_github_login(access_token: str) -> dict:
    user = _fetch_github_user(access_token)
    github_user_id = str(user.get("id", "")).strip()
    username = str(user.get("login", "")).strip()
    avatar_url = str(user.get("avatar_url", "")).strip()
    if not github_user_id or not username:
        raise HTTPException(status_code=502, detail="GitHub user profile was incomplete")
    stored_user = upsert_github_user(github_user_id, username, avatar_url)
    upsert_github_credential(
        stored_user["id"],
        username,
        access_token,
        token_type="bearer",
        scope_info="repo",
    )
    return {
        "user": stored_user,
        "username": username,
        "avatar_url": avatar_url,
    }


def _log_http_error(event: str, request_id: str, status_code: int, detail: object) -> None:
    log_event(
        event,
        request_id,
        status_code=status_code,
        detail=sanitize_for_log(detail),
    )


def _query_impl(
    body: QueryRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    session_token: str | None = None,
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
    previous_repo = os.getenv("RETRIEVAL_REPO_ROOT", "")
    previous_collection = os.getenv("QDRANT_COLLECTION_NAME", "")
    auth_user = _current_auth_user(session_token)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        provider_config = get_active_provider_credential(auth_user["id"])
    except ValueError as e:
        if "authentication failed" in str(e):
            raise HTTPException(
                status_code=400,
                detail="Your active provider API key cannot be decrypted. This happens when the server encryption key changes. Please delete and re-add your provider API key, or switch/provide a valid encryption key in settings.",
            )
        raise
    if not provider_config:
        raise HTTPException(
            status_code=400,
            detail="No active provider credential configured for this user",
        )
    
    # Check for client-side model override header
    model_override = request.headers.get("x-app-model-override", "").strip()
    if model_override:
        provider_config["model"] = model_override
    session = _resolve_query_session(body.session_id, auth_user)
    thread = None
    if body.thread_id:
        thread = get_thread(body.thread_id.strip())
        if not thread or not _thread_visible_to_user(thread, auth_user):
            raise HTTPException(status_code=404, detail="Thread not found")
        if session and thread.get("repo_session_id") != session["id"]:
            raise HTTPException(status_code=409, detail="Thread does not belong to the selected session")
    elif session:
        thread = ensure_default_thread(session["id"], user_id=auth_user["id"] if auth_user else "")
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
        if thread and session:
            query_memory = ThreadConversationMemory(thread["id"], session["id"], max_turns=5)
        elif session:
            query_memory = SessionConversationMemory(session["id"], max_turns=5)
        else:
            query_memory = memory
        with _query_lock:
            answer, sources, token_count, meta = run_query(
                query_text,
                query_memory,
                request_id=request_id,
                return_meta=True,
                provider_config=provider_config,
            )
        if session:
            if thread:
                append_thread_message(thread["id"], session["id"], "user", query_text)
                append_thread_message(
                    thread["id"],
                    session["id"],
                    "assistant",
                    answer,
                    sources=sources,
                    context_tokens=token_count,
                )
            else:
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
            "evidence_confidence": meta.get("evidence_confidence", {}).get("level", "strong"),
            "metrics": {
                "total_latency_ms": total_ms,
                "stage_latency_ms": meta.get("stage_latency_ms", {}),
                "source_filter": meta.get("source_filter", {}),
            },
        }
    except LlmProviderError as exc:
        total_ms = int((time.perf_counter() - started) * 1000)
        observe_api_request(path, str(exc.status_code), total_ms)
        RETRIEVAL_ERRORS_TOTAL.labels(error_type="provider").inc()
        log_event(
            "api.query.error",
            request_id,
            error=exc.detail,
            status_code=exc.status_code,
            total_latency_ms=total_ms,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
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


@v1.get("/crypto/submission-key", response_model=SubmissionPublicKeyResponse)
def submission_public_key_v1() -> SubmissionPublicKeyResponse:
    return SubmissionPublicKeyResponse(
        key_id=get_submission_key_id(),
        algorithm="RSA-OAEP-256",
        public_key_pem=get_submission_public_key_pem(),
    )


@v1.get("/metrics")
def metrics_v1() -> Response:
    body, content_type = render_prometheus_metrics()
    return Response(content=body, media_type=content_type)


@v1.get("/ragas/latest", response_model=RagasValidationArtifactResponse)
def ragas_latest_v1(
    authorization: str | None = Header(default=None),
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> RagasValidationArtifactResponse:
    _require_auth(authorization)
    _require_auth_user(session_token)
    return RagasValidationArtifactResponse(**load_ragas_validation_bundle())


@v1.post("/query")
def query_v1(
    body: QueryRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    return _query_impl(body, request, authorization, x_request_id, session_token)


@v1.get("/provider-credentials")
def list_provider_credentials_v1(
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    user = _require_auth_user(session_token)
    return {"provider_credentials": _enrich_provider_runtime_list(list_provider_credentials(user["id"]))}


@v1.post("/provider-credentials")
def create_provider_credential_v1(
    body: ProviderCredentialCreateRequest,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    user = _require_auth_user(session_token)
    provider = body.provider.strip().lower()
    api_key = _resolve_submitted_secret(body.api_key, body.encrypted_secret)
    model = (body.model or "").strip()
    label = (body.label or "").strip()
    if provider not in SUPPORTED_PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    if not provider or not label or (provider != "local" and not api_key):
        raise HTTPException(status_code=400, detail="provider, label, and api_key are required")
    existing = list_provider_credentials(user["id"])
    should_be_active = body.is_active if body.is_active is not None else not existing
    record = create_provider_credential(
        user["id"],
        provider,
        label,
        api_key,
        model=model,
        set_active=should_be_active,
    )
    if provider == "local" and should_be_active:
        background_prime_primary_model()
    log_event(
        "api.provider_credential.created",
        new_request_id(),
        user_id=user["id"],
        provider=provider,
        label=label,
        is_active=should_be_active,
    )
    return {"provider_credential": _enrich_provider_runtime(record)}


@v1.post("/provider-credentials/{credential_id}/activate")
def activate_provider_credential_v1(
    credential_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    user = _require_auth_user(session_token)
    try:
        record = set_active_provider_credential(user["id"], credential_id)
    except ValueError as e:
        if "authentication failed" in str(e):
            raise HTTPException(
                status_code=400,
                detail="The selected provider API key cannot be decrypted. This happens when the server encryption key changes. Please delete and re-add this credential, or switch/provide a valid encryption key in settings.",
            )
        raise
    if not record:
        raise HTTPException(status_code=404, detail="Provider credential not found")
    record.pop("api_key", None)
    if record.get("provider") == "local":
        background_prime_primary_model()
    return {"provider_credential": _enrich_provider_runtime(record)}


@v1.delete("/provider-credentials/{credential_id}")
def delete_provider_credential_v1(
    credential_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    user = _require_auth_user(session_token)
    deleted = delete_provider_credential(user["id"], credential_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider credential not found")
    return {"deleted": True, "credential_id": credential_id}


@v1.post("/sessions")
def create_session_v1(
    body: SessionCreateRequest,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    tenant_id = (body.tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    github_token = (body.github_token or "").strip()
    if not github_token and auth_user:
        try:
            stored_github = get_github_credential(auth_user["id"])
            if stored_github:
                github_token = stored_github["access_token"]
        except ValueError as e:
            if "authentication failed" in str(e):
                raise HTTPException(
                    status_code=400,
                    detail="Your stored GitHub token cannot be decrypted (encryption key changed). Please reconnect your GitHub account in settings.",
                )
            raise

    # Validate LLM provider readiness *before* starting indexing.
    # Normal deterministic ingestion never requires a provider.
    provider_config: dict | None = None
    if body.enable_chunk_descriptions:
        try:
            provider_config = require_llm_ready_for_user(auth_user["id"])
        except ProviderNotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ProviderNotReadyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    import sqlite3
    try:
        session = create_session(
            repo_full_name=body.repo_full_name.strip(),
            tenant_id=tenant_id,
            repo_url=(body.repo_url or "").strip(),
            github_token=github_token,
            user_id=auth_user["id"],
            enable_chunk_descriptions=body.enable_chunk_descriptions,
            provider_config=provider_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise HTTPException(
                status_code=503,
                detail="Backend database is initializing. Please retry in a moment.",
            ) from exc
        raise
    log_event(
        "api.session.created",
        new_request_id(),
        session_id=session["id"],
        repo_full_name=session["repo_full_name"],
        tenant_id=session["tenant_id"],
        user_id=auth_user["id"],
    )
    return {"session": session}


@v1.get("/github/repos")
def list_github_repos_v1(
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    user = _require_auth_user(session_token)
    try:
        credential = get_github_credential(user["id"])
    except ValueError as e:
        if "authentication failed" in str(e):
            raise HTTPException(
                status_code=400,
                detail="Your stored GitHub token cannot be decrypted (encryption key changed). Please reconnect your GitHub account in settings.",
                )
        raise
    if not credential:
        raise HTTPException(status_code=404, detail="No GitHub credential connected for this user")
    try:
        repos = _fetch_github_repos(credential["access_token"])
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or str(exc)
        raise HTTPException(status_code=502, detail=f"GitHub repo fetch failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub repo fetch network error: {exc}") from exc
    return {"repos": repos}


@v1.get("/sessions")
def list_sessions_v1(
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    sessions = [s for s in list_sessions() if _session_visible_to_user(s, auth_user)]
    return {"sessions": sessions}


@v1.get("/sessions/{session_id}")
def get_session_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session}


@v1.get("/sessions/{session_id}/indexing-options")
def get_session_indexing_options_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    try:
        from retrieval.session_indexer import get_session_indexing_options
        options = get_session_indexing_options(session_id, auth_user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"session_id": session_id, "indexing_options": options}


@v1.patch("/sessions/{session_id}/indexing-options")
def patch_session_indexing_options_v1(
    session_id: str,
    body: SessionIndexingOptionsUpdateRequest,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    try:
        from retrieval.session_indexer import update_session_indexing_options
        options = update_session_indexing_options(
            session_id, auth_user["id"], refine_labels_with_llm=body.refine_labels_with_llm
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"session_id": session_id, "indexing_options": options}


@v1.get("/sessions/{session_id}/messages")
def list_session_messages_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": list_session_messages(session_id)}


@v1.get("/sessions/{session_id}/threads")
def list_session_threads_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    threads = [t for t in list_threads_for_session(session_id) if _thread_visible_to_user(t, auth_user)]
    return {"threads": threads}


@v1.post("/sessions/{session_id}/threads")
def create_session_thread_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    count = len(list_threads_for_session(session_id)) + 1
    thread = create_thread(
        session_id,
        user_id=auth_user["id"],
        title=f"Thread {count}",
    )
    return {"thread": thread}


@v1.get("/threads/{thread_id}/messages")
def list_thread_messages_v1(
    thread_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    thread = get_thread(thread_id)
    if not thread or not _thread_visible_to_user(thread, auth_user):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"messages": list_thread_messages(thread_id)}


@v1.delete("/threads/{thread_id}/messages")
def clear_thread_messages_v1(
    thread_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    thread = get_thread(thread_id)
    if not thread or not _thread_visible_to_user(thread, auth_user):
        raise HTTPException(status_code=404, detail="Thread not found")
    deleted = clear_thread_messages(thread_id)
    return {"cleared": deleted, "thread_id": thread_id}


@v1.delete("/sessions/{session_id}/messages")
def clear_session_messages_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    deleted = clear_session_messages(session_id)
    return {"cleared": deleted, "session_id": session_id}


@v1.delete("/sessions/{session_id}")
def delete_session_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    deleted = delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True, "session_id": session_id}


@v1.post("/sessions/{session_id}/retry")
def retry_session_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("enable_chunk_descriptions") or session.get("refine_labels_with_llm"):
        try:
            provider_config = require_llm_ready_for_user(auth_user["id"])
            from retrieval.session_indexer import _session_provider_configs
            _session_provider_configs[session["id"]] = provider_config
        except ProviderNotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ProviderNotReadyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    session = retry_indexing(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    log_event(
        "api.session.retry",
        new_request_id(),
        session_id=session["id"],
        status=session["status"],
        user_id=auth_user["id"],
    )
    return {"session": session}


@v1.get("/sessions/{session_id}/repo-status")
def get_session_repo_status_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    try:
        from retrieval.session_indexer import get_session_repo_status
        status_info = get_session_repo_status(session_id, auth_user["id"])
        return status_info
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@v1.get("/sessions/{session_id}/evaluation/latest")
def get_latest_evaluation_report_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    
    from retrieval.eval_reports import get_latest_evaluation_report
    return get_latest_evaluation_report(session_id)



@v1.post("/sessions/{session_id}/index-latest")
def index_latest_session_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") == "indexing":
        raise HTTPException(status_code=409, detail="Session is already indexing")

    if session.get("enable_chunk_descriptions") or session.get("refine_labels_with_llm"):
        try:
            provider_config = require_llm_ready_for_user(auth_user["id"])
            from retrieval.session_indexer import _session_provider_configs
            _session_provider_configs[session["id"]] = provider_config
        except ProviderNotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ProviderNotReadyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        from retrieval.session_indexer import index_latest_version
        res = index_latest_version(session_id, auth_user["id"])
        log_event(
            "api.session.index_latest",
            new_request_id(),
            session_id=session_id,
            user_id=auth_user["id"],
        )
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@v1.get("/sessions/{session_id}/indexing-events")
def list_indexing_events_v1(
    session_id: str,
    after_id: int = 0,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    """Return stored indexing progress events for a session."""
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")
    events = get_indexing_events(session_id, after_id=after_id)
    return {"events": events}


@v1.get("/sessions/{session_id}/indexing-events/stream")
def stream_indexing_events_v1(
    session_id: str,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> StreamingResponse:
    """SSE stream of live indexing progress events."""
    auth_user = _require_auth_user(session_token)
    session = get_session(session_id)
    if not session or not _session_visible_to_user(session, auth_user):
        raise HTTPException(status_code=404, detail="Session not found")

    import json as _json

    def _event_generator():
        for event in subscribe_indexing_events(session_id):
            if event.get("_heartbeat"):
                yield ": heartbeat\n\n"
                continue
            payload = _json.dumps(event, default=str)
            yield f"id: {event['id']}\nevent: indexing\ndata: {payload}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _oauth_popup_html(*, success: bool, error: str = "") -> str:
    """Return a minimal HTML page that postMessages the auth result to the opener popup."""
    frontend_origin = CODESEEK_FRONTEND_URL.rstrip("/")
    safe_error = error.replace("\\", "\\\\").replace("'", "\\'").replace("<", "&lt;").replace(">", "&gt;")
    status = "success" if success else "error"
    icon = "&#10003;" if success else "&#10007;"
    msg_text = "Connected! Closing&hellip;" if success else "Login failed. Closing&hellip;"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>GitHub Login</title>
  <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      height:100vh;background:#0a0a0a;color:#a3a3a3;font-size:13px;gap:10px}}
    .icon{{font-size:28px;color:{'#22c55e' if success else '#ef4444'}}}
  </style>
</head>
<body>
  <span class="icon">{icon}</span>
  <p>{msg_text}</p>
  <script>
    (function(){{
      try{{
        if(window.opener&&!window.opener.closed){{
          window.opener.postMessage(
            {{type:'CODESEEK_GITHUB_AUTH',status:'{status}',error:'{safe_error}'}},
            '{frontend_origin}'
          );
        }}
      }}catch(e){{}}
      setTimeout(function(){{window.close();}},600);
    }})();
  </script>
</body>
</html>"""


@app.get("/auth/github/login", response_model=None)
def auth_github_login() -> HTMLResponse | RedirectResponse:
    """Start GitHub OAuth: generate CSRF state cookie and redirect browser to GitHub."""
    try:
        client_id, _, redirect_uri = _github_oauth_config()
    except HTTPException as exc:
        return HTMLResponse(content=_oauth_popup_html(success=False, error=str(exc.detail)), status_code=200)

    if not redirect_uri:
        redirect_uri = f"{CODESEEK_FRONTEND_URL.rstrip('/')}/auth/github/callback"

    state = secrets.token_urlsafe(32)
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "repo",
        "state": state,
    })
    response = RedirectResponse(
        url=f"https://github.com/login/oauth/authorize?{params}",
        status_code=302,
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        secure=AUTH_SESSION_SECURE_COOKIE,
        samesite="lax",
        max_age=300,
        path="/",
    )
    return response


@app.get("/auth/github/callback", response_model=None)
def auth_github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    """GitHub OAuth callback: verify CSRF state, exchange code, set session cookie, close popup."""
    # User cancelled or GitHub error
    if error:
        msg = error_description or error
        return HTMLResponse(content=_oauth_popup_html(success=False, error=msg))

    # CSRF state check
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE, "")
    if not code or not state or not cookie_state or not hmac.compare_digest(cookie_state, state):
        return HTMLResponse(content=_oauth_popup_html(success=False, error="Invalid or expired OAuth state. Please try again."))

    request_id = new_request_id()
    try:
        token_data = _exchange_github_code(code)
        access_token = str(token_data.get("access_token", "")).strip()
    except HTTPException as exc:
        _log_http_error("api.auth.github.error", request_id, exc.status_code, exc.detail)
        return HTMLResponse(content=_oauth_popup_html(success=False, error=str(exc.detail)))
    except Exception as exc:
        _log_http_error("api.auth.github.error", request_id, 502, str(exc))
        return HTMLResponse(content=_oauth_popup_html(success=False, error="GitHub OAuth failed. Please try again."))

    try:
        persisted = _persist_github_login(access_token)
    except Exception as exc:
        _log_http_error("api.auth.github.error", request_id, 502, str(exc))
        return HTMLResponse(content=_oauth_popup_html(success=False, error="GitHub profile fetch failed."))

    session_token, _session = create_auth_session(persisted["user"]["id"])
    log_event(
        "api.auth.github.success",
        request_id,
        user_id=persisted["user"]["id"],
        username=persisted["username"],
    )
    html_response = HTMLResponse(content=_oauth_popup_html(success=True))
    html_response.set_cookie(AUTH_SESSION_COOKIE, session_token, **_cookie_settings())
    html_response.delete_cookie(OAUTH_STATE_COOKIE, path="/")
    return html_response


@app.post("/auth/github")
def auth_github(body: GithubAuthCodeRequest, response: Response) -> dict:
    request_id = new_request_id()
    code = body.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    try:
        token_data = _exchange_github_code(code)
        access_token = str(token_data.get("access_token", "")).strip()
    except HTTPException:
        _log_http_error("api.auth.github.error", request_id, 400, "code exchange rejected")
        raise
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or str(exc)
        _log_http_error("api.auth.github.error", request_id, 502, detail)
        raise HTTPException(status_code=502, detail=f"GitHub OAuth request failed: {detail}") from exc
    except httpx.HTTPError as exc:
        _log_http_error("api.auth.github.error", request_id, 502, str(exc))
        raise HTTPException(status_code=502, detail=f"GitHub OAuth network error: {exc}") from exc

    persisted = _persist_github_login(access_token)
    session_token, _session = create_auth_session(persisted["user"]["id"])
    response.set_cookie(AUTH_SESSION_COOKIE, session_token, **_cookie_settings())
    log_event(
        "api.auth.github.success",
        request_id,
        user_id=persisted["user"]["id"],
        username=persisted["username"],
    )

    return {
        "authenticated": True,
        "username": persisted["username"],
        "avatar_url": persisted["avatar_url"],
    }


@app.post("/auth/github/token")
def auth_github_token(body: GithubTokenConnectRequest, response: Response) -> dict:
    request_id = new_request_id()
    access_token = _resolve_submitted_secret(body.access_token, body.encrypted_secret)
    if not access_token:
        raise HTTPException(status_code=400, detail="access_token is required")
    try:
        persisted = _persist_github_login(access_token)
    except HTTPException:
        _log_http_error("api.auth.github_token.error", request_id, 400, "token rejected")
        raise
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or str(exc)
        _log_http_error("api.auth.github_token.error", request_id, 400, detail)
        raise HTTPException(status_code=400, detail=f"GitHub token validation failed: {detail}") from exc
    except httpx.HTTPError as exc:
        _log_http_error("api.auth.github_token.error", request_id, 502, str(exc))
        raise HTTPException(status_code=502, detail=f"GitHub token validation network error: {exc}") from exc

    session_token, _session = create_auth_session(persisted["user"]["id"])
    response.set_cookie(AUTH_SESSION_COOKIE, session_token, **_cookie_settings())
    log_event(
        "api.auth.github_token.success",
        request_id,
        user_id=persisted["user"]["id"],
        username=persisted["username"],
    )
    return {
        "authenticated": True,
        "username": persisted["username"],
        "avatar_url": persisted["avatar_url"],
    }


@app.get("/auth/me")
def auth_me(session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE)) -> dict:
    user = _current_auth_user(session_token)
    if not user:
        return {"authenticated": False}
    try:
        github_connected = bool(get_github_credential(user["id"]))
    except ValueError:
        github_connected = False
    return {
        "authenticated": True,
        "user": user,
        "github_connected": github_connected,
    }


@app.post("/auth/logout")
def auth_logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    deleted = delete_auth_session(session_token or "")
    response.delete_cookie(AUTH_SESSION_COOKIE, path="/")
    return {"logged_out": True, "session_cleared": deleted}


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
    session_token: str | None = Cookie(default=None, alias=AUTH_SESSION_COOKIE),
) -> dict:
    return _query_impl(body, request, authorization, x_request_id, session_token)


app.include_router(v1)
