"""Repository loading stage."""

import os
from pathlib import Path
from urllib.parse import urlparse

from rag_ingestion.config import TEMP_CLONE_DIR


def load_repository(source: str) -> dict:
    """Resolve a local repository path or clone a GitHub repository."""
    source_path = Path(source).expanduser()
    if source_path.exists() and source_path.is_dir():
        repository_root = source_path.resolve()
        return {
            "repository_name": repository_root.name,
            "repository_root": str(repository_root),
            "source_type": "local",
        }

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"} and parsed.netloc in {
        "github.com",
        "www.github.com",
    }:
        from git import Repo

        repo_name = Path(parsed.path.removesuffix(".git")).name
        destination = Path(TEMP_CLONE_DIR) / repo_name
        if destination.exists():
            raise FileExistsError(f"Clone destination already exists: {destination}")

        clone_url, token = _build_clone_url(source)
        try:
            Repo.clone_from(clone_url, destination)
        except Exception as exc:
            error_text = str(exc)
            if token:
                error_text = error_text.replace(token, "***")
            raise RuntimeError(f"Failed to clone repository: {error_text}") from exc

        return {
            "repository_name": repo_name,
            "repository_root": str(destination.resolve()),
            "source_type": "github",
        }

    raise ValueError(f"Source is not a local directory or GitHub URL: {source}")


def _build_clone_url(source: str) -> tuple[str, str]:
    parsed = urlparse(source)
    if parsed.username:
        return source, ""

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""
    if not token:
        return source, ""

    with_token = parsed._replace(netloc=f"x-access-token:{token}@{parsed.netloc}")
    return with_token.geturl(), token
