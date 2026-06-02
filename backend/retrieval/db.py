"""Minimal SQLite-backed persistence for repo sessions and chat history."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.getenv("CODESEEK_DB_PATH", "/tmp/codeseek.sqlite3")).resolve()

_init_lock = threading.Lock()
_initialized = False
_initialized_path: Path | None = None


def get_db_path() -> Path:
    """Read DB path at runtime to support tests overriding env."""
    raw = os.getenv("CODESEEK_DB_PATH", "").strip()
    return Path(raw).resolve() if raw else DB_PATH


def init_db() -> None:
    global _initialized, _initialized_path
    current_path = get_db_path()
    if _initialized and _initialized_path == current_path:
        return
    with _init_lock:
        current_path = get_db_path()
        if _initialized and _initialized_path == current_path:
            return
        db_path = current_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repo_sessions (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    repo_full_name TEXT NOT NULL,
                    repo_url TEXT NOT NULL,
                    repo_root TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    job_started_at TEXT NOT NULL DEFAULT '',
                    job_finished_at TEXT NOT NULL DEFAULT '',
                    last_indexed_commit TEXT NOT NULL DEFAULT '',
                    chunks_generated INTEGER NOT NULL DEFAULT 0,
                    embeddings_stored INTEGER NOT NULL DEFAULT 0,
                    idempotent_reuse INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    context_tokens INTEGER,
                    is_error INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES repo_sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_repo_sessions_tenant_repo
                    ON repo_sessions(tenant_id, repo_full_name);

                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
                    ON chat_messages(session_id, created_at);
                """
            )
        _initialized = True
        _initialized_path = db_path


@contextmanager
def db_cursor():
    init_db()
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        cursor = conn.cursor()
        yield conn, cursor
        conn.commit()
    finally:
        conn.close()
