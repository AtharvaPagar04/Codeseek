import os
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi import Response

from retrieval import api_service


class ApiServiceGithubAuthTests(unittest.TestCase):
    def test_github_oauth_config_requires_server_config(self) -> None:
        with patch.dict(os.environ, {"GITHUB_CLIENT_ID": "", "GITHUB_CLIENT_SECRET": ""}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                api_service._github_oauth_config()

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("GitHub OAuth is not configured", ctx.exception.detail)

    def test_exchange_github_code_returns_access_token(self) -> None:
        token_response = Mock()
        token_response.raise_for_status.return_value = None
        token_response.json.return_value = {"access_token": "gho_test"}

        with patch.dict(
            os.environ,
            {
                "GITHUB_CLIENT_ID": "client-id",
                "GITHUB_CLIENT_SECRET": "client-secret",
                "GITHUB_REDIRECT_URI": "http://localhost:5173/auth/callback",
            },
            clear=False,
        ), patch("retrieval.api_service.httpx.post", return_value=token_response) as http_post:
            data = api_service._exchange_github_code("abc123")

        self.assertEqual(data["access_token"], "gho_test")
        http_post.assert_called_once()
        _, kwargs = http_post.call_args
        self.assertEqual(kwargs["json"]["client_id"], "client-id")
        self.assertEqual(kwargs["json"]["client_secret"], "client-secret")
        self.assertEqual(kwargs["json"]["code"], "abc123")
        self.assertEqual(kwargs["json"]["redirect_uri"], "http://localhost:5173/auth/callback")

    def test_fetch_github_user_returns_profile_json(self) -> None:
        user_response = Mock()
        user_response.raise_for_status.return_value = None
        user_response.json.return_value = {"login": "octocat", "avatar_url": "https://avatars.example/octocat.png"}

        with patch("retrieval.api_service.httpx.get", return_value=user_response) as http_get:
            data = api_service._fetch_github_user("gho_test")

        self.assertEqual(data["login"], "octocat")
        self.assertEqual(data["avatar_url"], "https://avatars.example/octocat.png")
        http_get.assert_called_once()

    def test_persist_github_login_stores_user_and_credential(self) -> None:
        github_user = {"id": 12345, "login": "octocat", "avatar_url": "https://avatars.example/octocat.png"}
        with patch("retrieval.api_service._fetch_github_user", return_value=github_user), \
             patch("retrieval.api_service.upsert_github_user", return_value={"id": "user-1"}), \
             patch("retrieval.api_service.upsert_github_credential") as upsert_credential:
            persisted = api_service._persist_github_login("ghp_secret")

        self.assertEqual(persisted["username"], "octocat")
        upsert_credential.assert_called_once()
        _, kwargs = upsert_credential.call_args
        self.assertEqual(kwargs["token_type"], "bearer")

    def test_auth_me_returns_unauthenticated_without_cookie(self) -> None:
        response = api_service.auth_me(None)
        self.assertEqual(response, {"authenticated": False})

    def test_auth_logout_clears_cookie(self) -> None:
        response = Response()
        payload = api_service.auth_logout(response, None)
        self.assertTrue(payload["logged_out"])
        self.assertFalse(payload["session_cleared"])


if __name__ == "__main__":
    unittest.main()
