import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from retrieval import api_service, auth_store, session_indexer


class SessionIndexingOptionsApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp_dir.name) / "codeseek.sqlite3")
        self.env_patcher = patch.dict(
            os.environ,
            {
                "CODESEEK_DB_PATH": self.db_path,
                "CODESEEK_API_KEY": "backend-key",
                "CODESEEK_APP_ENCRYPTION_KEY": "test-encryption-key",
            },
            clear=False,
        )
        self.env_patcher.start()
        # Mock index enqueueing so indexing isn't run asynchronously in the background.
        self.enqueue_patcher = patch(
            "retrieval.session_indexer._enqueue_index_job",
            return_value=None,
        )
        self.enqueue_patcher.start()

    def tearDown(self):
        self.enqueue_patcher.stop()
        self.env_patcher.stop()
        self.tmp_dir.cleanup()

    def test_default_options_is_false(self):
        user = auth_store.upsert_github_user("user1-gh", "user1", "")
        session_token, _ = auth_store.create_auth_session(user["id"], ttl_seconds=3600)

        session = session_indexer.create_session(
            repo_full_name="octocat/hello-world",
            tenant_id="local",
            user_id=user["id"],
        )

        # GET options
        response = api_service.get_session_indexing_options_v1(
            session["id"],
            session_token=session_token,
        )

        self.assertEqual(response["session_id"], session["id"])
        self.assertEqual(
            response["indexing_options"],
            {"refine_labels_with_llm": False},
        )

    def test_patch_options_persists(self):
        user = auth_store.upsert_github_user("user1-gh", "user1", "")
        session_token, _ = auth_store.create_auth_session(user["id"], ttl_seconds=3600)

        session = session_indexer.create_session(
            repo_full_name="octocat/hello-world",
            tenant_id="local",
            user_id=user["id"],
        )

        # PATCH to true
        update_req = api_service.SessionIndexingOptionsUpdateRequest(
            refine_labels_with_llm=True
        )
        response = api_service.patch_session_indexing_options_v1(
            session["id"],
            body=update_req,
            session_token=session_token,
        )

        self.assertEqual(response["session_id"], session["id"])
        self.assertEqual(
            response["indexing_options"],
            {"refine_labels_with_llm": True},
        )

        # Confirm persisted in GET
        response_get = api_service.get_session_indexing_options_v1(
            session["id"],
            session_token=session_token,
        )
        self.assertEqual(
            response_get["indexing_options"],
            {"refine_labels_with_llm": True},
        )

        # Confirm serialized session contains refinement field
        session_loaded = session_indexer.get_session(session["id"])
        self.assertTrue(session_loaded["refine_labels_with_llm"])
        self.assertEqual(
            session_loaded["indexing_options"],
            {"refine_labels_with_llm": True},
        )

    def test_unauthorized_user_is_forbidden(self):
        user1 = auth_store.upsert_github_user("user1-gh", "user1", "")
        user2 = auth_store.upsert_github_user("user2-gh", "user2", "")
        session_token1, _ = auth_store.create_auth_session(user1["id"], ttl_seconds=3600)
        session_token2, _ = auth_store.create_auth_session(user2["id"], ttl_seconds=3600)

        session = session_indexer.create_session(
            repo_full_name="octocat/hello-world",
            tenant_id="local",
            user_id=user1["id"],
        )

        # Access user1's session options using user2's session token
        with self.assertRaises(HTTPException) as ctx:
            api_service.get_session_indexing_options_v1(
                session["id"],
                session_token=session_token2,
            )
        self.assertEqual(ctx.exception.status_code, 403)

        # Try to PATCH using user2's token
        update_req = api_service.SessionIndexingOptionsUpdateRequest(
            refine_labels_with_llm=True
        )
        with self.assertRaises(HTTPException) as ctx:
            api_service.patch_session_indexing_options_v1(
                session["id"],
                body=update_req,
                session_token=session_token2,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_missing_session_returns_404(self):
        user = auth_store.upsert_github_user("user1-gh", "user1", "")
        session_token, _ = auth_store.create_auth_session(user["id"], ttl_seconds=3600)

        with self.assertRaises(HTTPException) as ctx:
            api_service.get_session_indexing_options_v1(
                "nonexistent-session-id",
                session_token=session_token,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_pipeline_receives_correct_refinement_parameter(self):
        user = auth_store.upsert_github_user("user1-gh", "user1", "")

        # Test with refine_labels_with_llm = True
        session = session_indexer.create_session(
            repo_full_name="octocat/hello-world",
            tenant_id="local",
            user_id=user["id"],
        )
        session_indexer.update_session_indexing_options(
            session["id"],
            user["id"],
            refine_labels_with_llm=True
        )

        with patch("retrieval.session_indexer.run_pipeline") as mock_run_pipeline, \
             patch("retrieval.session_indexer._clone_or_pull", return_value="commit123"), \
             patch("retrieval.session_indexer._collection_point_count", return_value=1):
            mock_run_pipeline.return_value = SimpleNamespace(
                chunks_generated=1, embeddings_stored=1, collection=session["collection"]
            )
            session_indexer._index_job(session["id"])
            mock_run_pipeline.assert_called_once()
            _, kwargs = mock_run_pipeline.call_args
            self.assertTrue(kwargs.get("enable_llm_label_refinement"))

        # Test with refine_labels_with_llm = False
        session2 = session_indexer.create_session(
            repo_full_name="octocat/other-repo",
            tenant_id="local",
            user_id=user["id"],
        )

        with patch("retrieval.session_indexer.run_pipeline") as mock_run_pipeline, \
             patch("retrieval.session_indexer._clone_or_pull", return_value="commit123"), \
             patch("retrieval.session_indexer._collection_point_count", return_value=1):
            mock_run_pipeline.return_value = SimpleNamespace(
                chunks_generated=1, embeddings_stored=1, collection=session2["collection"]
            )
            session_indexer._index_job(session2["id"])
            mock_run_pipeline.assert_called_once()
            _, kwargs = mock_run_pipeline.call_args
            self.assertFalse(kwargs.get("enable_llm_label_refinement"))


if __name__ == "__main__":
    unittest.main()
