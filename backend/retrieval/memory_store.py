"""Session-level rolling conversation memory persistence."""

from __future__ import annotations

from datetime import datetime, timezone

from retrieval.db import db_cursor
from retrieval.thread_store import ensure_default_thread


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_session_memory(session_id: str) -> dict:
    thread = ensure_default_thread(session_id)
    return get_thread_memory(thread["id"])


def get_thread_memory(thread_id: str) -> dict:
    with db_cursor() as (_conn, cursor):
        row = cursor.execute(
            """
            SELECT thread_id, rolling_summary, last_compacted_at, last_resolved_query
            FROM thread_memory
            WHERE thread_id = ?
            """,
            (thread_id,),
        ).fetchone()
    if not row:
        return {
            "thread_id": thread_id,
            "rolling_summary": "",
            "last_compacted_at": "",
            "last_resolved_query": "",
        }
    return {
        "thread_id": row["thread_id"],
        "rolling_summary": row["rolling_summary"] or "",
        "last_compacted_at": row["last_compacted_at"] or "",
        "last_resolved_query": row["last_resolved_query"] or "",
    }


def save_session_memory(
    session_id: str,
    *,
    rolling_summary: str,
    last_resolved_query: str,
    last_compacted_at: str | None = None,
) -> dict:
    thread = ensure_default_thread(session_id)
    return save_thread_memory(
        thread["id"],
        rolling_summary=rolling_summary,
        last_resolved_query=last_resolved_query,
        last_compacted_at=last_compacted_at,
    )


def save_thread_memory(
    thread_id: str,
    *,
    rolling_summary: str,
    last_resolved_query: str,
    last_compacted_at: str | None = None,
) -> dict:
    now = last_compacted_at or _now()
    existing = get_thread_memory(thread_id)
    with db_cursor() as (_conn, cursor):
        if existing["last_compacted_at"] or existing["last_resolved_query"] or existing["rolling_summary"]:
            cursor.execute(
                """
                UPDATE thread_memory
                SET rolling_summary = ?, last_compacted_at = ?, last_resolved_query = ?
                WHERE thread_id = ?
                """,
                (rolling_summary, now, last_resolved_query, thread_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO thread_memory (
                    thread_id, rolling_summary, last_compacted_at, last_resolved_query
                ) VALUES (?, ?, ?, ?)
                """,
                (thread_id, rolling_summary, now, last_resolved_query),
            )
    return {
        "thread_id": thread_id,
        "rolling_summary": rolling_summary,
        "last_compacted_at": now,
        "last_resolved_query": last_resolved_query,
    }


def clear_session_memory(session_id: str) -> bool:
    thread = ensure_default_thread(session_id)
    return clear_session_memory_for_thread(thread["id"])


def clear_session_memory_for_thread(thread_id: str) -> bool:
    with db_cursor() as (_conn, cursor):
        cursor.execute("DELETE FROM thread_memory WHERE thread_id = ?", (thread_id,))
        return bool(cursor.rowcount)
