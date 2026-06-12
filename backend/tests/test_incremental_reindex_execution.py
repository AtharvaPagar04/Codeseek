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


def test_incremental_indexing_detailed_behaviors(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "test_codeseek_detailed.sqlite3"
    monkeypatch.setenv("CODESEEK_DB_PATH", str(db_path))
    init_db(force=True)

    repo_dir = tmp_path / "repo_detailed"
    repo_dir.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)

    # 3 files
    (repo_dir / "unchanged.py").write_text("print('unchanged')", encoding="utf-8")
    (repo_dir / "modified.py").write_text("print('original modified')", encoding="utf-8")
    (repo_dir / "deleted.py").write_text("print('deleted')", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True)

    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), text=True).strip()
    active_branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_dir), text=True).strip()

    # Insert indexing job and session
    from retrieval.db import create_indexing_job
    with db_cursor() as (conn, cursor):
        cursor.execute(
            """
            INSERT INTO repo_sessions (
                id, tenant_id, user_id, repo_full_name, repo_url, repo_root, collection, status, created_at, updated_at, last_indexed_commit, current_branch, indexed_branch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sess-detailed",
                "tenant-1",
                "user-123",
                "owner/repo",
                "https://github.com/owner/repo",
                str(repo_dir),
                "col-detailed",
                "ready",
                "2026-06-12T00:00:00Z",
                "2026-06-12T00:00:00Z",
                commit_sha,
                active_branch,
                active_branch,
            ),
        )

    # Insert files into DB session_files
    unchanged_rec = upsert_session_file(
        session_id="sess-detailed",
        repo_path="unchanged.py",
        file_hash=hashlib.sha256(b"print('unchanged')").hexdigest(),
        indexed_commit_sha=commit_sha,
        indexed_branch=active_branch,
        status="indexed",
        last_indexed_at="2026-06-12T00:00:00Z",
    )
    from retrieval.db import replace_session_file_chunks
    replace_session_file_chunks(unchanged_rec["id"], [
        {"chunk_id": "chunk-unchanged", "vector_id": "vector-unchanged", "symbol": None, "start_line": 1, "end_line": 1}
    ])

    modified_rec = upsert_session_file(
        session_id="sess-detailed",
        repo_path="modified.py",
        file_hash=hashlib.sha256(b"print('original modified')").hexdigest(),
        indexed_commit_sha=commit_sha,
        indexed_branch=active_branch,
        status="indexed",
        last_indexed_at="2026-06-12T00:00:00Z",
    )
    replace_session_file_chunks(modified_rec["id"], [
        {"chunk_id": "chunk-modified-old", "vector_id": "vector-modified-old", "symbol": None, "start_line": 1, "end_line": 1}
    ])

    deleted_rec = upsert_session_file(
        session_id="sess-detailed",
        repo_path="deleted.py",
        file_hash=hashlib.sha256(b"print('deleted')").hexdigest(),
        indexed_commit_sha=commit_sha,
        indexed_branch=active_branch,
        status="indexed",
        last_indexed_at="2026-06-12T00:00:00Z",
    )
    replace_session_file_chunks(deleted_rec["id"], [
        {"chunk_id": "chunk-deleted", "vector_id": "vector-deleted", "symbol": None, "start_line": 1, "end_line": 1}
    ])

    # Mock pipeline operations
    monkeypatch.setattr(pipeline_main, "embed_chunks", lambda chunks, counters: chunks)
    monkeypatch.setattr("retrieval.isolation.validate_collection_binding", lambda *a, **kw: None)
    monkeypatch.setattr(session_indexer, "_clone_or_pull", lambda *args, **kwargs: commit_sha)

    mock_store = MagicMock()
    mock_delete = MagicMock()
    monkeypatch.setattr(storage_stage, "store_chunks", mock_store)
    monkeypatch.setattr(storage_stage, "delete_vectors_by_ids", mock_delete)

    # Perform modifications
    (repo_dir / "modified.py").write_text("print('new modified')", encoding="utf-8")
    (repo_dir / "deleted.py").unlink()
    (repo_dir / "added.py").write_text("print('added')", encoding="utf-8")

    # Create background job to trace counters
    job = create_indexing_job("sess-detailed", "incremental")
    job_id = job["id"]

    # Run execution
    run_incremental_reindex("sess-detailed", job_id=job_id)

    # Assertions
    # 1. Added files create new metadata and vector mappings
    files = list_session_files("sess-detailed", include_deleted=True)
    files_by_path = {f["repo_path"]: f for f in files}
    assert "added.py" in files_by_path
    assert files_by_path["added.py"]["status"] == "indexed"
    assert len(files_by_path["added.py"]["chunks"]) == 1

    # 2. Modified files replace only their own mappings
    assert "modified.py" in files_by_path
    assert files_by_path["modified.py"]["status"] == "indexed"
    assert files_by_path["modified.py"]["chunks"][0]["chunk_id"] != "chunk-modified-old"

    # 3. Deleted files remove known vectors and mark file deleted
    assert "deleted.py" in files_by_path
    assert files_by_path["deleted.py"]["status"] == "deleted"
    assert files_by_path["deleted.py"]["deleted_at"] is not None

    # 4. Unchanged files are not parsed, chunked, embedded, deleted, or remapped
    assert "unchanged.py" in files_by_path
    assert files_by_path["unchanged.py"]["status"] == "indexed"
    assert files_by_path["unchanged.py"]["chunks"][0]["chunk_id"] == "chunk-unchanged"
    assert files_by_path["unchanged.py"]["chunks"][0]["vector_id"] == "vector-unchanged"

    # 5. Incremental job counters reflect only changed work
    with db_cursor() as (conn, cursor):
        cursor.execute("SELECT status, files_indexed FROM indexing_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
    assert row is not None
    job_status, job_files_indexed = row
    assert job_status == "succeeded"
    assert job_files_indexed == 2
    deleted_vectors = []
    for call in mock_delete.call_args_list:
        deleted_vectors.extend(call[0][0])
    assert "vector-modified-old" in deleted_vectors
    assert "vector-deleted" in deleted_vectors
    assert "vector-unchanged" not in deleted_vectors

    # 6. Branch mismatch blocks incremental
    with db_cursor() as (conn, cursor):
        cursor.execute("UPDATE repo_sessions SET indexed_branch = 'other-branch' WHERE id = 'sess-detailed'")
    
    plan_mismatch = session_indexer.build_incremental_reindex_plan("sess-detailed")
    assert not plan_mismatch["can_incremental_reindex"]
    assert "Branch mismatch" in plan_mismatch["reason"]

    # 7. Full Index latest remains fallback
    preview = session_indexer.get_session_index_preview("sess-detailed", "user-123")
    assert preview["can_index_latest"] is True
    assert preview["can_incremental_reindex"] is False
