"""File discovery stage."""

import os
from pathlib import Path

from rag_ingestion.models.file import FileRecord
from rag_ingestion.utils.counters import PipelineCounters


def discover_files(
    repository_root: str, counters: PipelineCounters
) -> list[FileRecord]:
    """Walk a repository and return every file as a FileRecord."""
    root = Path(repository_root).resolve()

    if not root.exists() or not root.is_dir():
        raise ValueError(f"Repository root does not exist or is not a directory: {root}")

    files: list[FileRecord] = []

    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            path = Path(dirpath) / filename
            relative_path = path.relative_to(root).as_posix()
            stat = path.stat()

            files.append(
                FileRecord(
                    path=str(path.resolve()),
                    relative_path=relative_path,
                    extension=path.suffix,
                    size_bytes=stat.st_size,
                )
            )
            counters.files_discovered += 1

    return files