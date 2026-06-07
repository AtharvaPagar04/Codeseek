"""Session initialization and async repo indexing orchestration."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient

from rag_ingestion.main import run_pipeline
from retrieval.config import QDRANT_HOST, QDRANT_PORT
from retrieval.db import db_cursor, init_db
from retrieval.isolation import expected_collection_name
from retrieval.searcher import invalidate_lexical_index
from retrieval.thread_store import ensure_default_thread

WORKSPACE_ROOT = Path(
    os.getenv("CODESEEK_REPO_WORKSPACE", "/tmp/codeseek_repo_workspace")
).resolve()

_lock = threading.RLock()
_jobs: dict[str, threading.Thread] = {}
_session_tokens: dict[str, str] = {}
# Per-session provider credentials stored in memory only (never persisted to DB).
_session_provider_configs: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    out = []
    for ch in value.lower():
        out.append(ch if ch.isalnum() else "_")
    return "".join(out).strip("_") or "unknown"


def _load_state() -> dict:
    init_db()
    with db_cursor() as (_conn, cursor):
        rows = cursor.execute(
            """
            SELECT
                id, tenant_id, user_id, repo_full_name, repo_url, repo_root, collection, status, error,
                created_at, updated_at, job_started_at, job_finished_at, last_indexed_commit,
                chunks_generated, embeddings_stored, idempotent_reuse, enable_chunk_descriptions
            FROM repo_sessions
            ORDER BY created_at ASC
            """
        ).fetchall()
    return {"sessions": [_row_to_session(row) for row in rows]}


def _save_state(state: dict) -> None:
    init_db()
    sessions = state.get("sessions", [])
    with db_cursor() as (_conn, cursor):
        cursor.execute("DELETE FROM repo_sessions")
        for session in sessions:
            cursor.execute(
                """
                INSERT INTO repo_sessions (
                    id, tenant_id, user_id, repo_full_name, repo_url, repo_root, collection, status, error,
                    created_at, updated_at, job_started_at, job_finished_at, last_indexed_commit,
                    chunks_generated, embeddings_stored, idempotent_reuse, enable_chunk_descriptions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _session_insert_values(session),
            )


def list_sessions() -> list[dict]:
    with _lock:
        return _load_state().get("sessions", [])


def get_session(session_id: str) -> dict | None:
    with _lock:
        for session in _load_state().get("sessions", []):
            if session["id"] == session_id:
                return session
    return None


def delete_session(session_id: str) -> bool:
    with _lock:
        state = _load_state()
        sessions = state.get("sessions", [])
        session_to_delete = next((s for s in sessions if s.get("id") == session_id), None)
        if not session_to_delete:
            return False
        next_sessions = [s for s in sessions if s.get("id") != session_id]
        state["sessions"] = next_sessions
        _save_state(state)
        _session_tokens.pop(session_id, None)
        _session_provider_configs.pop(session_id, None)

        collection = session_to_delete.get("collection")
        if collection:
            try:
                client = QdrantClient(
                    QDRANT_HOST,
                    port=QDRANT_PORT,
                    timeout=5.0,
                    check_compatibility=False,
                )
                client.delete_collection(collection_name=collection)
            except Exception as e:
                # Log warning but do not crash/block the request
                print(f"Warning: failed to delete Qdrant collection {collection}: {e}")

        return True


def create_session(
    repo_full_name: str,
    tenant_id: str,
    repo_url: str = "",
    github_token: str = "",
    user_id: str = "",
    enable_chunk_descriptions: bool = False,
    provider_config: dict | None = None,
) -> dict:
    owner, _, name = repo_full_name.partition("/")
    if not owner or not name:
        raise ValueError("repo_full_name must be in 'owner/name' format")
    repo_slug = _slug(f"{owner}_{name}")
    repo_root = WORKSPACE_ROOT / _slug(tenant_id) / repo_slug
    collection = expected_collection_name(str(repo_root))
    session = {
        "id": uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "repo_full_name": repo_full_name,
        "repo_url": repo_url or f"https://github.com/{repo_full_name}.git",
        "repo_root": str(repo_root),
        "collection": collection,
        "status": "indexing",
        "error": "",
        "created_at": _now(),
        "updated_at": _now(),
        "job_started_at": "",
        "job_finished_at": "",
        "last_indexed_commit": "",
        "chunks_generated": 0,
        "embeddings_stored": 0,
        "idempotent_reuse": False,
        "enable_chunk_descriptions": enable_chunk_descriptions,
    }
    with _lock:
        state = _load_state()
        existing = _find_existing_session(
            state.get("sessions", []),
            repo_full_name=repo_full_name,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if existing:
            if github_token.strip():
                _session_tokens[existing["id"]] = github_token.strip()
            if provider_config:
                _session_provider_configs[existing["id"]] = provider_config
            ensure_default_thread(
                existing["id"],
                user_id=user_id,
                title=repo_full_name,
            )
            return existing
        state.setdefault("sessions", []).append(session)
        _save_state(state)
        if github_token.strip():
            _session_tokens[session["id"]] = github_token.strip()
        if provider_config:
            _session_provider_configs[session["id"]] = provider_config
    ensure_default_thread(
        session["id"],
        user_id=user_id,
        title=repo_full_name,
    )
    _enqueue_index_job(session["id"])
    return session


def _find_existing_session(
    sessions: list[dict],
    *,
    repo_full_name: str,
    tenant_id: str,
    user_id: str,
) -> dict | None:
    normalized_repo = repo_full_name.strip().lower()
    normalized_tenant = tenant_id.strip()
    normalized_user = user_id.strip()
    for session in sessions:
        if str(session.get("tenant_id", "")).strip() != normalized_tenant:
            continue
        if str(session.get("user_id", "")).strip() != normalized_user:
            continue
        if str(session.get("repo_full_name", "")).strip().lower() != normalized_repo:
            continue
        return session
    return None


def retry_indexing(session_id: str) -> dict | None:
    session = get_session(session_id)
    if not session:
        return None
    _update_session(
        session_id,
        status="indexing",
        error="",
        job_started_at="",
        job_finished_at="",
        idempotent_reuse=False,
    )
    _enqueue_index_job(session_id)
    return get_session(session_id)


def _enqueue_index_job(session_id: str) -> None:
    worker = threading.Thread(target=_index_job, args=(session_id,), daemon=True)
    _jobs[session_id] = worker
    worker.start()


def _update_session(session_id: str, **updates: object) -> dict | None:
    with _lock:
        session = get_session(session_id)
        if not session:
            return None
        session.update(updates)
        session["updated_at"] = _now()
        with db_cursor() as (_conn, cursor):
            cursor.execute(
                """
                UPDATE repo_sessions
                SET tenant_id = ?, user_id = ?, repo_full_name = ?, repo_url = ?, repo_root = ?, collection = ?,
                    status = ?, error = ?, created_at = ?, updated_at = ?, job_started_at = ?,
                    job_finished_at = ?, last_indexed_commit = ?, chunks_generated = ?,
                    embeddings_stored = ?, idempotent_reuse = ?, enable_chunk_descriptions = ?
                WHERE id = ?
                """,
                (
                    session["tenant_id"],
                    session.get("user_id", ""),
                    session["repo_full_name"],
                    session["repo_url"],
                    session["repo_root"],
                    session["collection"],
                    session["status"],
                    session["error"],
                    session["created_at"],
                    session["updated_at"],
                    session["job_started_at"],
                    session["job_finished_at"],
                    session["last_indexed_commit"],
                    int(session["chunks_generated"]),
                    int(session["embeddings_stored"]),
                    1 if session["idempotent_reuse"] else 0,
                    1 if session.get("enable_chunk_descriptions") else 0,
                    session_id,
                ),
            )
        return session
    return None


def _git_env(github_token: str = "") -> dict[str, str]:
    env = dict(os.environ)
    token = github_token.strip() or os.getenv("GITHUB_TOKEN", "").strip() or os.getenv("GH_TOKEN", "").strip()
    if token:
        env["GIT_ASKPASS"] = "echo"
        env["GITHUB_TOKEN"] = token
    return env


def _inject_token_url(url: str, github_token: str = "") -> str:
    token = github_token.strip() or os.getenv("GITHUB_TOKEN", "").strip() or os.getenv("GH_TOKEN", "").strip()
    if not token or "@github.com" in url:
        return url
    return url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")


def _run_git(args: list[str], cwd: Path | None = None, github_token: str = "") -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        env=_git_env(github_token),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "git command failed"
        raise RuntimeError(err)
    return proc.stdout.strip()


def _clone_or_pull(repo_url: str, repo_root: Path, github_token: str = "") -> str:
    repo_root.parent.mkdir(parents=True, exist_ok=True)
    auth_url = _inject_token_url(repo_url, github_token)
    if not (repo_root / ".git").exists():
        _run_git(["clone", auth_url, str(repo_root)], github_token=github_token)
    else:
        _run_git(["fetch", "--all", "--prune"], cwd=repo_root, github_token=github_token)
        _run_git(["pull", "--ff-only"], cwd=repo_root, github_token=github_token)
    return _run_git(["rev-parse", "HEAD"], cwd=repo_root, github_token=github_token)


def _collection_point_count(collection: str) -> int:
    client = QdrantClient(
        QDRANT_HOST,
        port=QDRANT_PORT,
        timeout=5.0,
        check_compatibility=False,
    )
    try:
        info = client.get_collection(collection)
    except Exception:
        return 0
    points = getattr(info, "points_count", None)
    return int(points or 0)


def _find_reusable_session(sessions: list[dict], current: dict, commit: str) -> dict | None:
    for session in sessions:
        if session["id"] == current["id"]:
            continue
        if session.get("status") != "ready":
            continue
        if session.get("tenant_id") != current.get("tenant_id"):
            continue
        if session.get("repo_full_name") != current.get("repo_full_name"):
            continue
        if session.get("last_indexed_commit") != commit:
            continue
        if _collection_point_count(session.get("collection", "")) <= 0:
            continue
        return session
    return None


def _index_job(session_id: str) -> None:
    from retrieval.indexing_events import emit_indexing_event

    session = get_session(session_id)
    if not session:
        return
    _update_session(session_id, status="indexing", job_started_at=_now(), error="")
    emit_indexing_event(session_id, "queued", "Indexing job started.")

    try:
        repo_root = Path(session["repo_root"])
        github_token = _session_tokens.get(session_id, "")

        emit_indexing_event(session_id, "loader", "Cloning or updating repository…")
        commit = _clone_or_pull(session["repo_url"], repo_root, github_token=github_token)

        all_sessions = list_sessions()
        reusable = _find_reusable_session(all_sessions, session, commit)
        if reusable:
            emit_indexing_event(
                session_id, "complete",
                "Repository already indexed at this commit. Reusing existing index.",
                level="success",
            )
            _update_session(
                session_id,
                status="ready",
                job_finished_at=_now(),
                last_indexed_commit=commit,
                collection=reusable["collection"],
                chunks_generated=0,
                embeddings_stored=0,
                idempotent_reuse=True,
            )
            return

        emit_indexing_event(session_id, "loader", "Preparing repository for indexing.")

        def _emit(stage, message, level="info", progress=None, total=None, metadata=None):
            emit_indexing_event(
                session_id, stage, message,
                level=level, progress=progress, total=total, metadata=metadata,
            )

        counters = run_pipeline(
            str(repo_root),
            collection_name=session["collection"],
            enable_chunk_descriptions=bool(session.get("enable_chunk_descriptions", False)),
            provider_config=_session_provider_configs.get(session_id),
            event_callback=_emit,
        )
        invalidate_lexical_index(session["collection"])
        stored = int(getattr(counters, "embeddings_stored", 0))
        if stored <= 0 and _collection_point_count(session["collection"]) <= 0:
            raise RuntimeError("Ingestion completed but no embeddings were stored")

        emit_indexing_event(
            session_id, "complete",
            f"Indexing complete — {stored} chunks stored.",
            level="success",
            progress=stored, total=stored,
        )
        _update_session(
            session_id,
            status="ready",
            job_finished_at=_now(),
            last_indexed_commit=commit,
            chunks_generated=int(getattr(counters, "chunks_generated", 0)),
            embeddings_stored=stored,
            idempotent_reuse=False,
        )
    except Exception as exc:
        try:
            emit_indexing_event(
                session_id, "failed",
                f"Indexing failed: {exc}",
                level="error",
            )
        except Exception:
            pass
        _update_session(
            session_id,
            status="failed",
            job_finished_at=_now(),
            error=str(exc),
        )


def _row_to_session(row) -> dict:
    try:
        enable_desc = bool(row["enable_chunk_descriptions"])
    except (KeyError, IndexError, TypeError):
        enable_desc = False

    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "user_id": row["user_id"],
        "repo_full_name": row["repo_full_name"],
        "repo_url": row["repo_url"],
        "repo_root": row["repo_root"],
        "collection": row["collection"],
        "status": row["status"],
        "error": row["error"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "job_started_at": row["job_started_at"] or "",
        "job_finished_at": row["job_finished_at"] or "",
        "last_indexed_commit": row["last_indexed_commit"] or "",
        "chunks_generated": int(row["chunks_generated"] or 0),
        "embeddings_stored": int(row["embeddings_stored"] or 0),
        "idempotent_reuse": bool(row["idempotent_reuse"]),
        "enable_chunk_descriptions": enable_desc,
    }


def _session_insert_values(session: dict) -> tuple:
    return (
        session["id"],
        session["tenant_id"],
        session.get("user_id", ""),
        session["repo_full_name"],
        session["repo_url"],
        session["repo_root"],
        session["collection"],
        session["status"],
        session["error"],
        session["created_at"],
        session["updated_at"],
        session["job_started_at"],
        session["job_finished_at"],
        session["last_indexed_commit"],
        int(session["chunks_generated"]),
        int(session["embeddings_stored"]),
        1 if session["idempotent_reuse"] else 0,
        1 if session.get("enable_chunk_descriptions") else 0,
    )
