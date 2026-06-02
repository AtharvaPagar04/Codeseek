"""File filtering stage."""

import fnmatch
from pathlib import Path

from rag_ingestion.models.file import FileRecord
from rag_ingestion.utils.counters import PipelineCounters

IGNORE_DIRS = {
    ".git",
    ".github",
    "node_modules",
    ".next",
    "dist",
    "build",
    "coverage",
    "venv",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}

IGNORE_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
    "Gemfile.lock",
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".DS_Store",
    "Thumbs.db",
}

IGNORE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".pdf",
    ".svg",
    ".zip",
    ".rar",
    ".tar",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pyc",
    ".pyo",
}

IGNORE_PATTERNS = {
    "*.min.js",
    "*.min.css",
    "*_generated.py",
    "*_pb2.py",
    "*.pb.go",
    "generated/*",
    "gen/*",
}


def filter_files(
    files: list[FileRecord], repo_root: str, counters: PipelineCounters
) -> list[FileRecord]:
    """Apply .gitignore and system ignore rules."""
    spec = _load_gitignore(repo_root)
    filtered: list[FileRecord] = []

    for file in files:
        if spec is not None and spec.match_file(file.relative_path):
            counters.files_ignored += 1
            continue

        if _is_system_ignored(file):
            counters.files_ignored += 1
            continue

        filtered.append(file)

    return filtered


def _load_gitignore(repo_root: str):
    gitignore = Path(repo_root) / ".gitignore"
    if not gitignore.exists():
        return None

    import pathspec

    with gitignore.open("r", encoding="utf-8", errors="ignore") as handle:
        return pathspec.PathSpec.from_lines("gitwildmatch", handle)


def _is_system_ignored(file: FileRecord) -> bool:
    path = Path(file.relative_path)
    parts = set(path.parts)
    if parts & IGNORE_DIRS:
        return True

    if path.name in IGNORE_FILENAMES:
        return True

    if file.extension.lower() in IGNORE_EXTENSIONS:
        return True

    return any(fnmatch.fnmatch(file.relative_path, pattern) for pattern in IGNORE_PATTERNS)
