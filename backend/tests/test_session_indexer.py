from pathlib import Path
from types import SimpleNamespace

from retrieval import session_indexer


def test_create_session_persists_indexing_state(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CODESEEK_DB_PATH", str(tmp_path / "codeseek.sqlite3"))
    monkeypatch.setattr(session_indexer, "WORKSPACE_ROOT", tmp_path / "repos")
    monkeypatch.setattr(session_indexer, "_enqueue_index_job", lambda _session_id: None)

    session = session_indexer.create_session(
        repo_full_name="octocat/hello-world",
        tenant_id="local",
    )

    assert session["status"] == "indexing"
    assert session["repo_full_name"] == "octocat/hello-world"
    assert session["collection"].startswith("repository_chunks__")
    all_sessions = session_indexer.list_sessions()
    assert len(all_sessions) == 1
    assert all_sessions[0]["id"] == session["id"]


def test_index_job_reuses_ready_session_for_same_commit(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CODESEEK_DB_PATH", str(tmp_path / "codeseek.sqlite3"))
    monkeypatch.setattr(session_indexer, "WORKSPACE_ROOT", tmp_path / "repos")
    monkeypatch.setattr(session_indexer, "_clone_or_pull", lambda _url, _root, github_token="": "abc123")
    monkeypatch.setattr(session_indexer, "_collection_point_count", lambda _collection: 10)
    monkeypatch.setattr(
        session_indexer,
        "run_pipeline",
        lambda _root, collection_name: SimpleNamespace(
            chunks_generated=0, embeddings_stored=0, collection=collection_name
        ),
    )

    monkeypatch.setattr(session_indexer, "_enqueue_index_job", lambda _session_id: None)
    ready = session_indexer.create_session("octocat/hello-world", "local")
    session_indexer._update_session(
        ready["id"],
        status="ready",
        last_indexed_commit="abc123",
        collection=ready["collection"],
    )

    pending = session_indexer.create_session("octocat/hello-world", "local")
    session_indexer._index_job(pending["id"])
    refreshed = session_indexer.get_session(pending["id"])
    assert refreshed is not None
    assert refreshed["status"] == "ready"
    assert refreshed["idempotent_reuse"] is True
    assert refreshed["last_indexed_commit"] == "abc123"


def test_delete_and_retry_helpers(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CODESEEK_DB_PATH", str(tmp_path / "codeseek.sqlite3"))
    monkeypatch.setattr(session_indexer, "WORKSPACE_ROOT", tmp_path / "repos")
    queued: list[str] = []
    monkeypatch.setattr(session_indexer, "_enqueue_index_job", lambda session_id: queued.append(session_id))

    session = session_indexer.create_session("octocat/hello-world", "local")
    assert queued == [session["id"]]

    retried = session_indexer.retry_indexing(session["id"])
    assert retried is not None
    assert retried["status"] == "indexing"
    assert queued == [session["id"], session["id"]]

    assert session_indexer.delete_session(session["id"]) is True
    assert session_indexer.get_session(session["id"]) is None
    assert session_indexer.delete_session(session["id"]) is False


def test_create_session_keeps_github_token_in_memory_only(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CODESEEK_DB_PATH", str(tmp_path / "codeseek.sqlite3"))
    monkeypatch.setattr(session_indexer, "WORKSPACE_ROOT", tmp_path / "repos")
    monkeypatch.setattr(session_indexer, "_enqueue_index_job", lambda _session_id: None)
    session_indexer._session_tokens.clear()

    session = session_indexer.create_session(
        repo_full_name="octocat/hello-world",
        tenant_id="local",
        github_token="ghp_secret",
    )

    assert session_indexer._session_tokens[session["id"]] == "ghp_secret"
    persisted = session_indexer.get_session(session["id"])
    assert persisted is not None
    assert "github_token" not in persisted


def test_index_job_uses_session_github_token(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CODESEEK_DB_PATH", str(tmp_path / "codeseek.sqlite3"))
    monkeypatch.setattr(session_indexer, "WORKSPACE_ROOT", tmp_path / "repos")
    monkeypatch.setattr(session_indexer, "_enqueue_index_job", lambda _session_id: None)
    used: dict[str, str] = {}

    def fake_clone(repo_url: str, repo_root: Path, github_token: str = "") -> str:
        used["repo_url"] = repo_url
        used["github_token"] = github_token
        return "abc123"

    monkeypatch.setattr(session_indexer, "_clone_or_pull", fake_clone)
    monkeypatch.setattr(session_indexer, "_collection_point_count", lambda _collection: 10)
    monkeypatch.setattr(
        session_indexer,
        "run_pipeline",
        lambda _root, collection_name: SimpleNamespace(
            chunks_generated=1, embeddings_stored=1, collection=collection_name
        ),
    )
    session_indexer._session_tokens.clear()

    session = session_indexer.create_session(
        "octocat/hello-world",
        "local",
        github_token="ghp_secret",
    )
    session_indexer._index_job(session["id"])

    assert used["repo_url"] == "https://github.com/octocat/hello-world.git"
    assert used["github_token"] == "ghp_secret"
