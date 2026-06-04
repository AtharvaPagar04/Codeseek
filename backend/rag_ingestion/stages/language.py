"""Language detection stage."""

from pathlib import Path

from rag_ingestion.models.file import FileRecord
from rag_ingestion.utils.counters import PipelineCounters
from rag_ingestion.utils.logger import log_skip

LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".txt": "text",
}


def detect_languages(
    files: list[FileRecord], counters: PipelineCounters
) -> list[FileRecord]:
    """Populate language for supported files and mark unsupported files."""
    for file in files:
        language = _detect_language(file)
        if language is None:
            file.skipped = True
            file.skip_reason = "unsupported_language"
            log_skip(file.relative_path, "unsupported_language", "skipped")
            counters.files_skipped_unsupported += 1
            continue

        file.language = language

    return files


def _detect_language(file: FileRecord) -> str | None:
    relative_path = file.relative_path.lower()
    filename = Path(file.relative_path).name.lower()

    if relative_path == "dockerfile" or filename == "dockerfile":
        return "dockerfile"
    if filename == ".env.example":
        return "env"
    if relative_path in {"requirements.txt", "readme.md", "readme.mdx", "pyproject.toml", "package.json"}:
        return LANGUAGE_MAP.get(file.extension.lower()) or {
            "requirements.txt": "text",
            "readme.md": "markdown",
            "readme.mdx": "markdown",
            "pyproject.toml": "toml",
            "package.json": "json",
        }.get(relative_path)

    return LANGUAGE_MAP.get(file.extension.lower())
