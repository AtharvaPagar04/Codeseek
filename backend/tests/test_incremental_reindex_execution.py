import os
import subprocess
import hashlib
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from retrieval.db import init_db, db_cursor, upsert_session_file, list_session_files
from retrieval.session_indexer import run_incremental_reindex, get_session
from rag_ingestion import main as pipeline_main
from rag_ingestion.stages import storage as storage_stage
from retrieval import session_indexer


def test_incremental_reindex_execution(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "test_codeseek.sqlite3"
    monkeypatch.setenv("CODESEEK_DB_PATH", str(db_path))
    init_db(force=True)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Initialize a real Git repository locally
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)

    app_path = repo_dir / "app.py"
    app_path.write_text("def foo():\n    return 42\n", encoding="utf-8")
    app_hash = hashlib.sha256(b"def foo():\n    return 42\n").hexdigest()

    subprocess.run(["git", "add", "app.py"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True)

    # Resolve commit SHA and active branch
    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), text=True).strip()
    active_branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_dir), text=True).strip()

    # Set up session rows in repo_sessions to satisfy foreign keys
    with db_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO repo_sessions (
                id, tenant_id, repo_full_name, repo_url, repo_root, collection, status, created_at, updated_at, last_indexed_commit, current_branch, indexed_branch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-a",
                "tenant-1",
                "owner/repo1",
                "https://github.com/owner/repo1",
                str(repo_dir),
                "col-a",
                "ready",
                "2026-06-12T00:00:00Z",
                "2026-06-12T00:00:00Z",
                commit_sha,
                active_branch,
                active_branch,
            ),
        )
        cursor.execute(
            """
            INSERT INTO repo_sessions (
                id, tenant_id, repo_full_name, repo_url, repo_root, collection, status, created_at, updated_at, last_indexed_commit, current_branch, indexed_branch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-b",
                "tenant-1",
                "owner/repo2",
                "https://github.com/owner/repo2",
                str(repo_dir),
                "col-b",
                "ready",
                "2026-06-12T00:00:00Z",
                "2026-06-12T00:00:00Z",
                commit_sha,
                active_branch,
                active_branch,
            ),
        )

    # Mock pipeline operations requiring external services
    monkeypatch.setattr(pipeline_main, "embed_chunks", lambda chunks, counters: chunks)
    monkeypatch.setattr("retrieval.isolation.validate_collection_binding", lambda *a, **kw: None)
    monkeypatch.setattr(session_indexer, "_clone_or_pull", lambda *args, **kwargs: commit_sha)

    mock_store = MagicMock()
    mock_delete = MagicMock()
    monkeypatch.setattr(storage_stage, "store_chunks", mock_store)
    monkeypatch.setattr(storage_stage, "delete_vectors_by_ids", mock_delete)

    # 1. Unavailable plan refuses incremental execution
    # First, let's clear the session_files metadata so the plan is empty/unavailable
    with pytest.raises(RuntimeError, match="No previously indexed files found"):
        run_incremental_reindex("session-a")
    
    sess_failed = get_session("session-a")
    assert sess_failed["status"] == "failed"
    assert "No previously indexed files" in sess_failed["error"]

    # Restore status to ready for next tests
    with db_cursor() as (conn, cursor):
        cursor.execute("UPDATE repo_sessions SET status = 'ready' WHERE id = 'session-a'")

    # Initialize app.py metadata in session_files
    file_record = upsert_session_file(
        session_id="session-a",
        repo_path="app.py",
        file_hash=app_hash,
        indexed_commit_sha=commit_sha,
        indexed_branch=active_branch,
        status="indexed",
        last_indexed_at="2026-06-12T00:00:00Z",
    )
    from retrieval.db import replace_session_file_chunks
    replace_session_file_chunks(file_record["id"], [
        {"chunk_id": "chunk-1", "vector_id": "vector-1", "symbol": "foo", "start_line": 1, "end_line": 2}
    ])

    # 2. Clean unchanged plan performs no Qdrant mutation
    mock_store.reset_mock()
    mock_delete.reset_mock()
    run_incremental_reindex("session-a")
    mock_store.assert_not_called()
    mock_delete.assert_not_called()
    assert get_session("session-a")["status"] == "ready"

    # 3. Added file creates new file metadata and chunk mappings
    new_path = repo_dir / "new_file.py"
    new_path.write_text("print('hello')", encoding="utf-8")

    mock_store.reset_mock()
    mock_delete.reset_mock()
    run_incremental_reindex("session-a")

    mock_store.assert_called_once()
    mock_delete.assert_not_called()  # no deletions for just an addition

    files_a = list_session_files("session-a")
    assert len(files_a) == 2
    paths = [f["repo_path"] for f in files_a]
    assert "new_file.py" in paths
    assert get_session("session-a")["status"] == "ready"

    # Clean up new_file.py from disk and DB
    new_path.unlink()
    with db_cursor() as (conn, cursor):
        cursor.execute("DELETE FROM session_files WHERE repo_path = 'new_file.py'")

    # 4. Modified file replaces old chunk mappings
    app_path.write_text("def foo():\n    return 43\n", encoding="utf-8")

    mock_store.reset_mock()
    mock_delete.reset_mock()
    run_incremental_reindex("session-a")

    mock_store.assert_called_once()
    mock_delete.assert_called_once_with(["vector-1"], collection_name="col-a")

    files_mod = list_session_files("session-a")
    assert len(files_mod) == 1
    assert files_mod[0]["repo_path"] == "app.py"
    assert len(files_mod[0]["chunks"]) > 0
    assert files_mod[0]["chunks"][0]["chunk_id"] != "chunk-1"
    assert get_session("session-a")["status"] == "ready"

    # Save the new vector IDs
    new_vector_ids = [c["vector_id"] for c in files_mod[0]["chunks"]]

    # 5. Deleted file deletes known vector IDs and marks file deleted
    app_path.unlink()

    mock_store.reset_mock()
    mock_delete.reset_mock()
    run_incremental_reindex("session-a")

    mock_store.assert_not_called()  # no additions/modifications
    mock_delete.assert_called_once_with(new_vector_ids, collection_name="col-a")

    files_del = list_session_files("session-a", include_deleted=True)
    assert len(files_del) == 1
    assert files_del[0]["status"] == "deleted"
    assert files_del[0]["deleted_at"] is not None
    assert get_session("session-a")["status"] == "ready"

    # Re-create app.py and mark it indexed in DB for next tests
    app_path.write_text("def foo():\n    return 42\n", encoding="utf-8")
    file_rec_reset = upsert_session_file(
        session_id="session-a",
        repo_path="app.py",
        file_hash=app_hash,
        indexed_commit_sha=commit_sha,
        indexed_branch=active_branch,
        status="indexed",
        last_indexed_at="2026-06-12T00:00:00Z",
    )
    replace_session_file_chunks(file_rec_reset["id"], [
        {"chunk_id": "chunk-reset", "vector_id": "vector-reset", "symbol": "foo", "start_line": 1, "end_line": 2}
    ])

    # 6. Unrelated session metadata is not affected
    plan_b = list_session_files("session-b", include_deleted=True)
    assert len(plan_b) == 0

    # 7. Failure during replacement does not mark session as successfully indexed
    app_path.write_text("def foo():\n    return 999\n", encoding="utf-8")

    def failing_store(*args, **kwargs):
        raise RuntimeError("Qdrant write failed!")

    monkeypatch.setattr(storage_stage, "store_chunks", failing_store)

    with pytest.raises(RuntimeError, match="Qdrant write failed!"):
        run_incremental_reindex("session-a")

    sess_failed = get_session("session-a")
    assert sess_failed["status"] == "failed"
    assert "Qdrant write failed!" in sess_failed["error"]
