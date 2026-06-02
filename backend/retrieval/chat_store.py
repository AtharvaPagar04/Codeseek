"""Persistence helpers for chat message history."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from retrieval.db import db_cursor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_session_messages(session_id: str) -> list[dict]:
    with db_cursor() as (_conn, cursor):
        rows = cursor.execute(
            """
            SELECT id, role, content, sources_json, context_tokens, is_error, created_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
    return [_row_to_message(row) for row in rows]


def append_message(
    session_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    context_tokens: int | None = None,
    *,
    is_error: bool = False,
) -> dict:
    message = {
        "id": uuid.uuid4().hex,
        "session_id": session_id,
        "role": role,
        "content": content,
        "sources": sources or [],
        "context_tokens": context_tokens,
        "error": is_error,
        "timestamp": _now(),
    }
    with db_cursor() as (_conn, cursor):
        cursor.execute(
            """
            INSERT INTO chat_messages (
                id, session_id, role, content, sources_json, context_tokens, is_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message["id"],
                session_id,
                role,
                content,
                json.dumps(message["sources"]),
                context_tokens,
                1 if is_error else 0,
                message["timestamp"],
            ),
        )
    return {
        "id": message["id"],
        "role": role,
        "content": content,
        "sources": message["sources"],
        "context_tokens": context_tokens,
        "error": is_error,
        "timestamp": message["timestamp"],
    }


def clear_session_messages(session_id: str) -> int:
    with db_cursor() as (_conn, cursor):
        cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        return int(cursor.rowcount or 0)


def _row_to_message(row) -> dict:
    sources_raw = row["sources_json"] or "[]"
    try:
        sources = json.loads(sources_raw)
        if not isinstance(sources, list):
            sources = []
    except Exception:
        sources = []
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "sources": sources,
        "context_tokens": row["context_tokens"],
        "error": bool(row["is_error"]),
        "timestamp": row["created_at"],
    }
