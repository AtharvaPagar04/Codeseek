"""Session initialization and async repo indexing orchestration."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qdrant_client import QdrantClient

from rag_ingestion.main import run_pipeline
from retrieval.config import INDEXING_STALE_AFTER_SECONDS, QDRANT_HOST, QDRANT_PORT
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


def _parse_timestamp(value: str) -> datetime | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_stale_indexing_session(session: dict, *, now: datetime | None = None) -> bool:
    if session.get("status") != "indexing":
        return False
    if INDEXING_STALE_AFTER_SECONDS <= 0:
        return False
    if int(session.get("files_indexed", 0) or 0) > 0:
        return False
    if int(session.get("chunks_generated", 0) or 0) > 0:
        return False
    if int(session.get("embeddings_stored", 0) or 0) > 0:
        return False
    updated_at = _parse_timestamp(str(session.get("updated_at", "") or ""))
    if not updated_at:
        return False
    current = now or datetime.now(timezone.utc)
    return current - updated_at > timedelta(seconds=INDEXING_STALE_AFTER_SECONDS)


def compute_repo_freshness_status(session: dict) -> str:
    status = session.get("status", "unknown")
    if status == "indexing":
        return "stale_indexing" if is_stale_indexing_session(session) else "indexing"
    if status == "failed":
        return "failed"
    current_sha = session.get("current_commit_sha", "")
    if not current_sha:
        return "unknown"
    if bool(session.get("repo_dirty")):
        return "dirty_worktree"
    if session.get("last_indexed_commit", "") == current_sha:
        return "up_to_date"
    return "out_of_date"


def _populate_repo_status(session: dict) -> dict:
    stale = is_stale_indexing_session(session)
    session["repo_status"] = {
        "status": "stale_indexing" if stale else compute_repo_freshness_status(session),
        "indexed_commit_sha": session.get("last_indexed_commit", ""),
        "current_commit_sha": session.get("current_commit_sha", ""),
        "current_branch": session.get("current_branch", ""),
        "dirty_worktree": bool(session.get("repo_dirty", False)),
        "checked_at": session.get("repo_status_checked_at", ""),
        "indexed_at": session.get("job_finished_at", ""),
        "files_indexed": int(session.get("files_indexed", 0)),
        "chunks_generated": int(session.get("chunks_generated", 0)),
        "embeddings_stored": int(session.get("embeddings_stored", 0)),
        "is_stale_indexing": stale,
        "error": session.get("error", ""),
    }
    return session


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
                chunks_generated, embeddings_stored, idempotent_reuse, enable_chunk_descriptions,
                refine_labels_with_llm, current_commit_sha, current_branch, repo_dirty,
                repo_status_checked_at, files_indexed
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
                    chunks_generated, embeddings_stored, idempotent_reuse, enable_chunk_descriptions,
                    refine_labels_with_llm, current_commit_sha, current_branch, repo_dirty,
                    repo_status_checked_at, files_indexed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _check_and_clean_stale_indexing_sessions(state: dict, exclude_session_id: str | None = None) -> None:
    """Checks for active indexing sessions. Marks any stale indexing sessions as failed."""
    sessions = state.get("sessions", [])
    stale_sessions = []
    has_active_indexing = False
    active_repo_name = ""

    for s in sessions:
        if s.get("status") == "indexing":
            if exclude_session_id and s.get("id") == exclude_session_id:
                continue
            job = _jobs.get(s["id"])
            if job and job.is_alive():
                has_active_indexing = True
                active_repo_name = s.get("repo_full_name", "another repository")
            elif is_stale_indexing_session(s):
                stale_sessions.append(s)

    if stale_sessions:
        for s in stale_sessions:
            s["status"] = "failed"
            s["error"] = "Indexing was interrupted (stale job detected)."
            s["updated_at"] = _now()
            _populate_repo_status(s)
        _save_state(state)

    if has_active_indexing:
        raise ValueError(
            f"Another repository ({active_repo_name}) is currently indexing. "
            "Only one repository indexing session is allowed at a time."
        )


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
        "current_commit_sha": "",
        "current_branch": "",
        "repo_dirty": False,
        "repo_status_checked_at": "",
        "files_indexed": 0,
    }
    _populate_repo_status(session)
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

        # Check if another session is already indexing
        _check_and_clean_stale_indexing_sessions(state)

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
    with _lock:
        state = _load_state()
        _check_and_clean_stale_indexing_sessions(state, exclude_session_id=session_id)
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
                    embeddings_stored = ?, idempotent_reuse = ?, enable_chunk_descriptions = ?,
                    refine_labels_with_llm = ?, current_commit_sha = ?, current_branch = ?,
                    repo_dirty = ?, repo_status_checked_at = ?, files_indexed = ?
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
                    1 if session.get("refine_labels_with_llm") else 0,
                    session.get("current_commit_sha", ""),
                    session.get("current_branch", ""),
                    bool(session.get("repo_dirty")),
                    session.get("repo_status_checked_at", ""),
                    int(session.get("files_indexed", 0)),
                    session_id,
                ),
            )
        return _populate_repo_status(session)
    return None


def _record_indexing_failure(
    session_id: str,
    exc: Exception,
    *,
    job_finished_at: str | None = None,
    chunks_generated: int | None = None,
    embeddings_stored: int | None = None,
    files_indexed: int | None = None,
    last_indexed_commit: str | None = None,
) -> dict | None:
    updates: dict[str, object] = {
        "status": "failed",
        "error": str(exc),
    }
    if job_finished_at is not None:
        updates["job_finished_at"] = job_finished_at
    if chunks_generated is not None:
        updates["chunks_generated"] = chunks_generated
    if embeddings_stored is not None:
        updates["embeddings_stored"] = embeddings_stored
    if files_indexed is not None:
        updates["files_indexed"] = files_indexed
    if last_indexed_commit is not None:
        updates["last_indexed_commit"] = last_indexed_commit
    return _update_session(session_id, **updates)


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


def _sync_local_workspace(src: Path, dest: Path) -> None:
    import shutil
    src = src.resolve()
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    
    ignore_dirs = {".venv", "node_modules", "__pycache__", ".pytest_cache", ".git"}
    
    # 1. Copy files/directories from src to dest
    for item in os.listdir(src):
        if item in ignore_dirs:
            continue
        src_item = src / item
        dest_item = dest / item
        try:
            if src_item.is_dir():
                if dest_item.exists():
                    shutil.rmtree(dest_item)
                shutil.copytree(
                    src_item,
                    dest_item,
                    ignore=shutil.ignore_patterns(".venv", "node_modules", "__pycache__", ".pytest_cache", "*.pyc", "*.pyo")
                )
            else:
                shutil.copy2(src_item, dest_item)
        except Exception as e:
            print(f"Warning: failed to sync {src_item} to {dest_item}: {e}")

    # 2. Copy the .git directory specially to make sure it's valid
    src_git = src / ".git"
    dest_git = dest / ".git"
    if src_git.exists():
        try:
            if dest_git.exists():
                shutil.rmtree(dest_git)
            shutil.copytree(src_git, dest_git)
        except Exception as e:
            print(f"Warning: failed to copy .git from {src_git} to {dest_git}: {e}")


def _clone_or_pull(repo_url: str, repo_root: Path, github_token: str = "") -> str:
    repo_root.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if we should sync from local workspace instead of cloning
    try:
        root_path = repo_root.resolve()
        tenant_id = root_path.parent.name
        repo_slug = root_path.name
        is_local_repo = (tenant_id == "local" and repo_slug == "atharvapagar04_codeseek")
    except Exception:
        is_local_repo = False

    if is_local_repo:
        local_src = Path(__file__).resolve().parent.parent.parent
        _sync_local_workspace(local_src, repo_root)
        return _run_git(["rev-parse", "HEAD"], cwd=repo_root)

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
    counters = None

    try:
        repo_root = Path(session["repo_root"])
        github_token = _session_tokens.get(session_id, "")

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

        provider_config = _session_provider_configs.get(session_id)
        if not provider_config and (bool(session.get("enable_chunk_descriptions")) or bool(session.get("refine_labels_with_llm"))):
            try:
                from retrieval.provider_health import require_llm_ready_for_user
                user_id = session.get("user_id", "")
                if user_id:
                    provider_config = require_llm_ready_for_user(user_id)
            except Exception as e:
                print(f"Warning: could not resolve LLM provider credential for session indexing: {e}")

        counters = run_pipeline(
            str(repo_root),
            collection_name=session["collection"],
            enable_chunk_descriptions=bool(session.get("enable_chunk_descriptions", False)),
            enable_llm_label_refinement=bool(session.get("refine_labels_with_llm", False)),
            provider_config=provider_config,
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
        _record_indexing_failure(
            session_id,
            exc,
            job_finished_at=_now(),
            chunks_generated=int(getattr(counters, "chunks_generated", 0)) if counters is not None else None,
            embeddings_stored=int(getattr(counters, "embeddings_stored", 0)) if counters is not None else None,
            files_indexed=int(getattr(counters, "files_parsed_ok", 0)) if counters is not None else None,
        )


def _row_to_session(row) -> dict:
    try:
        enable_desc = bool(row["enable_chunk_descriptions"])
    except (KeyError, IndexError, TypeError):
        enable_desc = False

    try:
        refine_labels = bool(row["refine_labels_with_llm"])
    except (KeyError, IndexError, TypeError):
        refine_labels = False

    def _get_val(k, default):
        try:
            return row[k]
        except (KeyError, IndexError, TypeError):
            return default

    session = {
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
        "refine_labels_with_llm": refine_labels,
        "indexing_options": {
            "refine_labels_with_llm": refine_labels,
        },
        "current_commit_sha": _get_val("current_commit_sha", ""),
        "current_branch": _get_val("current_branch", ""),
        "repo_dirty": bool(_get_val("repo_dirty", False)),
        "repo_status_checked_at": _get_val("repo_status_checked_at", ""),
        "files_indexed": int(_get_val("files_indexed", 0)),
    }
    return _populate_repo_status(session)


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
        1 if session.get("refine_labels_with_llm") else 0,
        session.get("current_commit_sha", ""),
        session.get("current_branch", ""),
        bool(session.get("repo_dirty")),
        session.get("repo_status_checked_at", ""),
        int(session.get("files_indexed", 0)),
    )


def get_session_indexing_options(session_id: str, user_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        raise ValueError("Session not found")
    if session.get("user_id", "") != user_id:
        raise PermissionError("Access denied")
    return {
        "refine_labels_with_llm": bool(session.get("refine_labels_with_llm", False))
    }


def update_session_indexing_options(
    session_id: str,
    user_id: str,
    *,
    refine_labels_with_llm: bool,
) -> dict:
    with _lock:
        session = get_session(session_id)
        if not session:
            raise ValueError("Session not found")
        if session.get("user_id", "") != user_id:
            raise PermissionError("Access denied")
        _update_session(session_id, refine_labels_with_llm=refine_labels_with_llm)
        return {
            "refine_labels_with_llm": refine_labels_with_llm
        }


def _run_git_command(repo_root: str, args: list[str], *, timeout: int = 20, github_token: str = "") -> str:
    env = dict(os.environ)
    token = github_token.strip() or os.getenv("GITHUB_TOKEN", "").strip() or os.getenv("GH_TOKEN", "").strip()
    if token:
        env["GIT_ASKPASS"] = "echo"
        env["GITHUB_TOKEN"] = token

    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Git command timeout: git {' '.join(args)}") from e

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "git command failed"
        if token and token in err:
            err = err.replace(token, "*****")
        raise RuntimeError(f"Git command failed: git {' '.join(args)}: {err}")
    return proc.stdout.strip()


def _get_local_git_status(repo_root: str, github_token: str = "") -> dict:
    current_commit_sha = _run_git_command(repo_root, ["rev-parse", "HEAD"], github_token=github_token)
    current_branch = _run_git_command(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], github_token=github_token)
    status_porcelain = _run_git_command(repo_root, ["status", "--porcelain"], github_token=github_token)
    dirty_worktree = bool(status_porcelain.strip())
    return {
        "current_commit_sha": current_commit_sha,
        "current_branch": current_branch,
        "dirty_worktree": dirty_worktree,
    }


def _refresh_remote_state(repo_root: str, github_token: str = "") -> None:
    _run_git_command(repo_root, ["fetch", "--all", "--prune"], github_token=github_token)


def _pull_latest(repo_root: str, github_token: str = "") -> dict:
    try:
        root_path = Path(repo_root).resolve()
        tenant_id = root_path.parent.name
        repo_slug = root_path.name
        is_local_repo = (tenant_id == "local" and repo_slug == "atharvapagar04_codeseek")
    except Exception:
        is_local_repo = False

    if is_local_repo:
        local_src = Path(__file__).resolve().parent.parent.parent
        _sync_local_workspace(local_src, Path(repo_root))
        return _get_local_git_status(repo_root, github_token=github_token)

    _run_git_command(repo_root, ["fetch", "--all", "--prune"], github_token=github_token)
    try:
        _run_git_command(repo_root, ["pull", "--ff-only"], github_token=github_token)
    except Exception as exc:
        raise RuntimeError(
            f"Git pull failed: {exc}. A non-fast-forward update or force-push may have occurred. "
            f"A clean re-clone/re-creation of the session is recommended."
        ) from exc
    return _get_local_git_status(repo_root, github_token=github_token)


def get_session_repo_status(session_id: str, user_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        raise ValueError("Session not found")
    if session.get("user_id", "") != user_id:
        raise PermissionError("Access denied")

    repo_root = session.get("repo_root", "")
    if not repo_root or not Path(repo_root).exists() or not (Path(repo_root) / ".git").exists():
        session["current_commit_sha"] = ""
        session["current_branch"] = ""
        session["repo_dirty"] = False
        session["repo_status_checked_at"] = _now()
        _update_session(
            session_id,
            current_commit_sha="",
            current_branch="",
            repo_dirty=False,
            repo_status_checked_at=session["repo_status_checked_at"],
        )
        return {
            "session_id": session_id,
            "repo_status": {
                "status": "unknown",
                "indexed_commit_sha": session.get("last_indexed_commit", ""),
                "current_commit_sha": "",
                "current_branch": "",
                "dirty_worktree": False,
                "checked_at": session["repo_status_checked_at"],
                "indexed_at": session.get("job_finished_at", ""),
                "files_indexed": int(session.get("files_indexed", 0)),
                "chunks_generated": int(session.get("chunks_generated", 0)),
                "embeddings_stored": int(session.get("embeddings_stored", 0)),
            }
        }

    github_token = _session_tokens.get(session_id, "")
    try:
        _refresh_remote_state(repo_root, github_token=github_token)
    except Exception as e:
        print(f"Warning: git fetch failed during freshness check: {e}")

    local_status = _get_local_git_status(repo_root, github_token=github_token)

    current_commit_sha = local_status["current_commit_sha"]
    try:
        upstream_branch = _run_git_command(repo_root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], github_token=github_token)
        if upstream_branch:
            current_commit_sha = _run_git_command(repo_root, ["rev-parse", "@{u}"], github_token=github_token)
    except Exception:
        pass

    session["current_commit_sha"] = current_commit_sha
    session["current_branch"] = local_status["current_branch"]
    session["repo_dirty"] = local_status["dirty_worktree"]
    session["repo_status_checked_at"] = _now()

    _update_session(
        session_id,
        current_commit_sha=session["current_commit_sha"],
        current_branch=session["current_branch"],
        repo_dirty=session["repo_dirty"],
        repo_status_checked_at=session["repo_status_checked_at"],
    )

    updated_session = get_session(session_id)
    return {
        "session_id": session_id,
        "repo_status": updated_session["repo_status"]
    }


def index_latest_version(session_id: str, user_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        raise ValueError("Session not found")
    if session.get("user_id", "") != user_id:
        raise PermissionError("Access denied")
    if session.get("status") == "indexing" and not is_stale_indexing_session(session):
        raise ValueError("Session is already indexing")

    with _lock:
        state = _load_state()
        _check_and_clean_stale_indexing_sessions(state, exclude_session_id=session_id)

    _update_session(
        session_id,
        status="indexing",
        error="",
        job_started_at="",
        job_finished_at="",
    )

    worker = threading.Thread(
        target=_index_latest_job,
        args=(session_id, user_id),
        daemon=True,
    )
    with _lock:
        _jobs[session_id] = worker
    worker.start()

    return {
        "session_id": session_id,
        "status": "indexing",
        "message": "Indexing latest repository version."
    }


def _index_latest_job(session_id: str, user_id: str) -> None:
    from retrieval.indexing_events import emit_indexing_event

    session = get_session(session_id)
    if not session:
        return

    _update_session(session_id, status="indexing", job_started_at=_now(), error="")
    emit_indexing_event(session_id, "queued", "Indexing latest repository version.")

    prev_status = session.get("status", "ready")
    prev_last_indexed_commit = session.get("last_indexed_commit", "")
    prev_chunks_generated = session.get("chunks_generated", 0)
    prev_embeddings_stored = session.get("embeddings_stored", 0)
    prev_files_indexed = session.get("files_indexed", 0)
    counters = None

    try:
        repo_root = Path(session["repo_root"])
        github_token = _session_tokens.get(session_id, "")

        emit_indexing_event(session_id, "loader", "Checking local repository state...")
        local_status = _get_local_git_status(str(repo_root), github_token=github_token)

        is_github_cloned = False
        try:
            is_github_cloned = repo_root.resolve().is_relative_to(WORKSPACE_ROOT.resolve())
        except ValueError:
            pass

        try:
            root_path = repo_root.resolve()
            tenant_id = root_path.parent.name
            repo_slug = root_path.name
            is_local_repo = (tenant_id == "local" and repo_slug == "atharvapagar04_codeseek")
        except Exception:
            is_local_repo = False

        if local_status["dirty_worktree"] and is_github_cloned and not is_local_repo:
            raise RuntimeError(
                "The repository workspace has uncommitted/dirty changes and cannot be pulled safely. "
                "Please recreate or clean the repository workspace."
            )

        emit_indexing_event(session_id, "loader", "Pulling latest changes from remote repository...")
        local_status = _pull_latest(str(repo_root), github_token=github_token)

        current_commit_sha = local_status["current_commit_sha"]
        try:
            upstream_branch = _run_git_command(
                str(repo_root),
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                github_token=github_token,
            )
            if upstream_branch:
                current_commit_sha = _run_git_command(
                    str(repo_root),
                    ["rev-parse", "@{u}"],
                    github_token=github_token,
                )
        except Exception:
            pass

        emit_indexing_event(session_id, "loader", "Preparing repository for full re-indexing.")

        def _emit(stage, message, level="info", progress=None, total=None, metadata=None):
            emit_indexing_event(
                session_id, stage, message,
                level=level, progress=progress, total=total, metadata=metadata,
            )

        provider_config = _session_provider_configs.get(session_id)
        if not provider_config and (bool(session.get("enable_chunk_descriptions")) or bool(session.get("refine_labels_with_llm"))):
            try:
                from retrieval.provider_health import require_llm_ready_for_user
                if user_id:
                    provider_config = require_llm_ready_for_user(user_id)
            except Exception as e:
                print(f"Warning: could not resolve LLM provider credential: {e}")

        counters = run_pipeline(
            str(repo_root),
            collection_name=session["collection"],
            enable_chunk_descriptions=bool(session.get("enable_chunk_descriptions", False)),
            enable_llm_label_refinement=bool(session.get("refine_labels_with_llm", False)),
            provider_config=provider_config,
            event_callback=_emit,
            recreate_collection=True,
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
            last_indexed_commit=current_commit_sha,
            current_commit_sha=current_commit_sha,
            current_branch=local_status["current_branch"],
            repo_dirty=local_status["dirty_worktree"],
            repo_status_checked_at=_now(),
            files_indexed=int(getattr(counters, "files_parsed_ok", 0)),
            chunks_generated=int(getattr(counters, "chunks_generated", 0)),
            embeddings_stored=stored,
            idempotent_reuse=False,
            error="",
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

        _record_indexing_failure(
            session_id,
            exc,
            job_finished_at=_now(),
            last_indexed_commit=prev_last_indexed_commit,
            chunks_generated=int(getattr(counters, "chunks_generated", prev_chunks_generated)) if counters is not None else prev_chunks_generated,
            embeddings_stored=int(getattr(counters, "embeddings_stored", prev_embeddings_stored)) if counters is not None else prev_embeddings_stored,
            files_indexed=int(getattr(counters, "files_parsed_ok", prev_files_indexed)) if counters is not None else prev_files_indexed,
        )


# Cleanup any stale indexing sessions left in the DB from a previous run/server crash.
try:
    init_db()
    with db_cursor() as (_conn, cursor):
        # At startup/import, all sessions marked as 'indexing' in the database are stale
        # because the new python process has no active threads running.
        cursor.execute(
            """
            UPDATE repo_sessions
            SET status = 'failed',
                error = 'Indexing was interrupted (server restarted).',
                updated_at = ?
            WHERE status = 'indexing'
            """,
            (_now(),)
        )
except Exception as e:
    # Do not block import if database is not initialized yet or throws an error.
    print(f"Warning: failed to clean up stale indexing sessions on startup: {e}")
