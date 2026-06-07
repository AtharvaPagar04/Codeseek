"""Database backend abstraction for SQLite and Postgres persistence."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency during local sqlite-only runs
    psycopg = None
    dict_row = None

SQLITE_DEFAULT_PATH = Path(os.getenv("CODESEEK_DB_PATH", "/tmp/codeseek.sqlite3")).resolve()

_init_lock = threading.Lock()
_initialized = False
_initialized_backend: str | None = None
_initialized_locator: str | None = None

_BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repo_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
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
    idempotent_reuse INTEGER NOT NULL DEFAULT 0,
    enable_chunk_descriptions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    github_user_id TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    avatar_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_github_credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    github_login TEXT NOT NULL,
    encrypted_access_token TEXT NOT NULL,
    token_type TEXT NOT NULL DEFAULT '',
    scope_info TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_provider_credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    encrypted_api_key TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    user_id TEXT DEFAULT NULL,
    repo_session_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(repo_session_id) REFERENCES repo_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    context_tokens INTEGER,
    is_error INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES repo_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS thread_memory (
    thread_id TEXT PRIMARY KEY,
    rolling_summary TEXT NOT NULL DEFAULT '',
    last_compacted_at TEXT NOT NULL DEFAULT '',
    last_resolved_query TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS thread_turn_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    primary_intent TEXT NOT NULL DEFAULT '',
    original_query TEXT NOT NULL DEFAULT '',
    resolved_query TEXT NOT NULL DEFAULT '',
    entities_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_thread_turn_entities_thread_turn
    ON thread_turn_entities(thread_id, turn_index);

CREATE INDEX IF NOT EXISTS idx_repo_sessions_tenant_repo
    ON repo_sessions(tenant_id, repo_full_name);

CREATE INDEX IF NOT EXISTS idx_repo_sessions_user_id
    ON repo_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
    ON chat_messages(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_created
    ON chat_messages(thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_threads_repo_session
    ON chat_threads(repo_session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id
    ON auth_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_user_provider_credentials_user_id
    ON user_provider_credentials(user_id);
"""


def _postgres_schema_sql() -> str:
    """Translate shared schema into Postgres-compatible DDL."""
    return _BASE_SCHEMA_SQL.replace(
        "id INTEGER PRIMARY KEY AUTOINCREMENT,",
        "id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,",
    )


def get_db_backend() -> str:
    explicit = os.getenv("CODESEEK_DB_BACKEND", "").strip().lower()
    if explicit in {"sqlite", "postgres"}:
        return explicit
    database_url = os.getenv("CODESEEK_DATABASE_URL", "").strip()
    return "postgres" if database_url.startswith("postgres") else "sqlite"


def get_db_path() -> Path:
    raw = os.getenv("CODESEEK_DB_PATH", "").strip()
    return Path(raw).resolve() if raw else SQLITE_DEFAULT_PATH


def get_database_locator() -> str:
    backend = get_db_backend()
    if backend == "postgres":
        return os.getenv("CODESEEK_DATABASE_URL", "").strip()
    return str(get_db_path())


def init_db(force: bool = False) -> None:
    global _initialized, _initialized_backend, _initialized_locator
    backend = get_db_backend()
    locator = get_database_locator()
    if backend == "sqlite" and not Path(locator).exists():
        force = True
    if not force and _initialized and _initialized_backend == backend and _initialized_locator == locator:
        return
    with _init_lock:
        if not force and _initialized and _initialized_backend == backend and _initialized_locator == locator:
            return
        if backend == "postgres":
            _init_postgres(locator)
        else:
            _init_sqlite(Path(locator).resolve())
        _initialized = True
        _initialized_backend = backend
        _initialized_locator = locator


def _init_sqlite(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_BASE_SCHEMA_SQL)
        repo_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(repo_sessions)").fetchall()
        }
        if "user_id" not in repo_columns:
            conn.execute(
                "ALTER TABLE repo_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
            )
        if "enable_chunk_descriptions" not in repo_columns:
            conn.execute(
                "ALTER TABLE repo_sessions ADD COLUMN enable_chunk_descriptions INTEGER NOT NULL DEFAULT 0"
            )
        message_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
        }
        if "thread_id" not in message_columns:
            conn.execute(
                "ALTER TABLE chat_messages ADD COLUMN thread_id TEXT NOT NULL DEFAULT ''"
            )


def _init_postgres(database_url: str) -> None:
    if not database_url:
        raise RuntimeError("CODESEEK_DATABASE_URL is required when CODESEEK_DB_BACKEND=postgres")
    if psycopg is None:
        raise RuntimeError("psycopg is required for Postgres support")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(_postgres_schema_sql())
            if not _postgres_has_column(cursor, "repo_sessions", "user_id"):
                cursor.execute(
                    "ALTER TABLE repo_sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
                )
            if not _postgres_has_column(cursor, "repo_sessions", "enable_chunk_descriptions"):
                cursor.execute(
                    "ALTER TABLE repo_sessions ADD COLUMN enable_chunk_descriptions INTEGER NOT NULL DEFAULT 0"
                )
            if not _postgres_has_column(cursor, "chat_messages", "thread_id"):
                cursor.execute(
                    "ALTER TABLE chat_messages ADD COLUMN thread_id TEXT NOT NULL DEFAULT ''"
                )
        conn.commit()


def _postgres_has_column(cursor, table_name: str, column_name: str) -> bool:
    row = cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    ).fetchone()
    return bool(row)


class _CursorWrapper:
    def __init__(self, cursor, *, backend: str):
        self._cursor = cursor
        self._backend = backend

    def execute(self, sql: str, params=None):
        if params is None:
            self._cursor.execute(self._sql(sql))
        else:
            self._cursor.execute(self._sql(sql), params)
        return self

    def executemany(self, sql: str, seq_of_params):
        self._cursor.executemany(self._sql(sql), seq_of_params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def _sql(self, sql: str) -> str:
        return _normalize_sql_placeholders(sql) if self._backend == "postgres" else sql


def _normalize_sql_placeholders(sql: str) -> str:
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


@contextmanager
def db_cursor():
    init_db()
    backend = get_db_backend()
    if backend == "postgres":
        database_url = get_database_locator()
        conn = psycopg.connect(database_url, row_factory=dict_row)
        try:
            raw_cursor = conn.cursor()
            cursor = _CursorWrapper(raw_cursor, backend="postgres")
            try:
                yield conn, cursor
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                raw_cursor.close()
        finally:
            conn.close()
        return

    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        raw_cursor = conn.cursor()
        cursor = _CursorWrapper(raw_cursor, backend="sqlite")
        try:
            yield conn, cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            raw_cursor.close()
    finally:
        conn.close()
