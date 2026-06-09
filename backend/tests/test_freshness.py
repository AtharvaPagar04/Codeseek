import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

from retrieval import api_service, auth_store, session_indexer


class SessionFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp_dir.name) / "codeseek.sqlite3")
        self.repo_workspace_dir = str(Path(self.tmp_dir.name) / "repo_workspace")
        os.makedirs(self.repo_workspace_dir, exist_ok=True)

        self.env_patcher = patch.dict(
            os.environ,
            {
                "CODESEEK_DB_PATH": self.db_path,
                "CODESEEK_API_KEY": "backend-key",
                "CODESEEK_APP_ENCRYPTION_KEY": "test-encryption-key",
                "CODESEEK_REPO_WORKSPACE": self.repo_workspace_dir,
            },
            clear=False,
        )
        self.env_patcher.start()

        # Patch WORKSPACE_ROOT dynamically to match our temporary workspace path
        self.workspace_root_patch = patch(
            "retrieval.session_indexer.WORKSPACE_ROOT",
            Path(self.repo_workspace_dir)
        )
        self.workspace_root_patch.start()

        # Mock _run_git_command to simulate git behavior
        self.git_cmd_patcher = patch(
            "retrieval.session_indexer._run_git_command"
        )
        self.mock_git_cmd = self.git_cmd_patcher.start()

        def git_side_effect(repo_root, cmd, github_token=""):
            if "rev-parse" in cmd:
                if "HEAD" in cmd:
                    if "--abbrev-ref" in cmd:
                        return "main"
                    return "commit123"
                if "@{u}" in cmd:
                    return "commit123"
            if "status" in cmd:
                return ""  # clean
            return ""
        self.mock_git_cmd.side_effect = git_side_effect

        # Mock remote state refresh
        self.remote_patcher = patch(
            "retrieval.session_indexer._refresh_remote_state",
            return_value=None
        )
        self.remote_patcher.start()

        # Mock enqueueing of background jobs
        self.enqueue_patcher = patch(
            "retrieval.session_indexer._enqueue_index_job",
            return_value=None,
        )
        self.enqueue_patcher.start()

        # Create dummy directories to pass existence checks.
        # Note: session_indexer._slug replaces hyphens with underscores, e.g. "octocat_hello_world"
        for name in ["octocat_hello_world", "octocat_fresh_repo"]:
            repo_dir = Path(self.repo_workspace_dir) / "local" / name
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / ".git").mkdir(exist_ok=True)

    def tearDown(self):
        self.enqueue_patcher.stop()
        self.remote_patcher.stop()
        self.git_cmd_patcher.stop()
        self.workspace_root_patch.stop()
        self.env_patcher.stop()
        self.tmp_dir.cleanup()

    def test_compute_repo_freshness_status(self):
        # 1. indexing
        status = session_indexer.compute_repo_freshness_status({
            "status": "indexing",
            "current_commit_sha": "abc",
            "last_indexed_commit": "abc",
            "repo_dirty": False
        })
        self.assertEqual(status, "indexing")

        # 2. failed
        status = session_indexer.compute_repo_freshness_status({
            "status": "failed",
            "current_commit_sha": "abc",
            "last_indexed_commit": "abc",
            "repo_dirty": False
        })
        self.assertEqual(status, "failed")

        # 3. unknown (empty current SHA)
        status = session_indexer.compute_repo_freshness_status({
            "status": "ready",
            "current_commit_sha": "",
            "last_indexed_commit": "abc",
            "repo_dirty": False
        })
        self.assertEqual(status, "unknown")

        # 4. dirty_worktree
        status = session_indexer.compute_repo_freshness_status({
            "status": "ready",
            "current_commit_sha": "abc",
            "last_indexed_commit": "abc",
            "repo_dirty": True
        })
        self.assertEqual(status, "dirty_worktree")

        # 5. up_to_date
        status = session_indexer.compute_repo_freshness_status({
            "status": "ready",
            "current_commit_sha": "abc",
            "last_indexed_commit": "abc",
            "repo_dirty": False
        })
        self.assertEqual(status, "up_to_date")

        # 6. out_of_date
        status = session_indexer.compute_repo_freshness_status({
            "status": "ready",
            "current_commit_sha": "def",
            "last_indexed_commit": "abc",
            "repo_dirty": False
        })
        self.assertEqual(status, "out_of_date")

    def test_get_session_repo_status_endpoint(self):
        user = auth_store.upsert_github_user("user1-gh", "user1", "")
        session_token, _ = auth_store.create_auth_session(user["id"], ttl_seconds=3600)

        session = session_indexer.create_session(
            repo_full_name="octocat/hello-world",
            tenant_id="local",
            user_id=user["id"],
        )
        session_indexer._update_session(session["id"], status="ready")

        # Retrieve status
        res = api_service.get_session_repo_status_v1(
            session_id=session["id"],
            session_token=session_token
        )

        self.assertIn("repo_status", res)
        repo_status = res["repo_status"]
        self.assertEqual(repo_status["status"], "out_of_date")  # Since last_indexed_commit is empty, and current_commit_sha is 'commit123'
        self.assertEqual(repo_status["current_branch"], "main")
        self.assertEqual(repo_status["current_commit_sha"], "commit123")
        self.assertFalse(repo_status["dirty_worktree"])

    def test_index_latest_unauthorized_hidden_404(self):
        user1 = auth_store.upsert_github_user("user1-gh", "user1", "")
        user2 = auth_store.upsert_github_user("user2-gh", "user2", "")
        session_token2, _ = auth_store.create_auth_session(user2["id"], ttl_seconds=3600)

        session = session_indexer.create_session(
            repo_full_name="octocat/hello-world",
            tenant_id="local",
            user_id=user1["id"],
        )
        session_indexer._update_session(session["id"], status="ready")

        with self.assertRaises(HTTPException) as ctx:
            api_service.index_latest_session_v1(
                session_id=session["id"],
                session_token=session_token2
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_repo_status_unauthorized_forbidden_403(self):
        user1 = auth_store.upsert_github_user("user1-gh", "user1", "")
        user2 = auth_store.upsert_github_user("user2-gh", "user2", "")
        session_token2, _ = auth_store.create_auth_session(user2["id"], ttl_seconds=3600)

        session = session_indexer.create_session(
            repo_full_name="octocat/hello-world",
            tenant_id="local",
            user_id=user1["id"],
        )

        with self.assertRaises(HTTPException) as ctx:
            api_service.get_session_repo_status_v1(
                session_id=session["id"],
                session_token=session_token2
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_index_latest_triggers_background_indexing(self):
        user = auth_store.upsert_github_user("user-fresh-gh", "user-fresh", "")
        session_token, _ = auth_store.create_auth_session(user["id"], ttl_seconds=3600)

        session = session_indexer.create_session(
            repo_full_name="octocat/fresh-repo",
            tenant_id="local",
            user_id=user["id"],
        )
        session_indexer._update_session(session["id"], status="ready")

        with patch("retrieval.session_indexer.index_latest_version") as mock_trigger:
            api_service.index_latest_session_v1(
                session_id=session["id"],
                session_token=session_token
            )
            mock_trigger.assert_called_once_with(session["id"], user["id"])


if __name__ == "__main__":
    unittest.main()
