"""Language detection stage."""

from rag_ingestion.models.file import FileRecord
from rag_ingestion.utils.counters import PipelineCounters
from rag_ingestion.utils.logger import log_skip

LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def detect_languages(
    files: list[FileRecord], counters: PipelineCounters
) -> list[FileRecord]:
    """Populate language for supported files and mark unsupported files."""
    for file in files:
        language = LANGUAGE_MAP.get(file.extension.lower())
        if language is None:
            file.skipped = True
            file.skip_reason = "unsupported_language"
            log_skip(file.relative_path, "unsupported_language", "skipped")
            counters.files_skipped_unsupported += 1
            continue

        file.language = language

    return files
