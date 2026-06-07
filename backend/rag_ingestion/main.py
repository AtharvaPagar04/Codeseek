"""Entry point for the RAG ingestion pipeline."""

import argparse

from rag_ingestion.config import (
    COLLECTION_NAME,
    ENABLE_INCREMENTAL_FILE_SKIP,
    RECREATE_COLLECTION_EACH_RUN,
)
from retrieval.isolation import expected_collection_name, validate_collection_binding
from rag_ingestion.stages.chunker import generate_chunks
from rag_ingestion.stages.discovery import discover_files
from rag_ingestion.stages.embedder import embed_chunks
from rag_ingestion.stages.filtering import filter_files
from rag_ingestion.stages.language import detect_languages
from rag_ingestion.stages.loader import load_repository
from rag_ingestion.stages.metadata import build_metadata
from rag_ingestion.stages.overflow import handle_overflow
from rag_ingestion.stages.parser import parse_file
from rag_ingestion.stages.repo_summary import (
    build_repo_summary_chunk,
    is_repo_summary_evidence_path,
)
from rag_ingestion.stages.storage import delete_chunks_for_paths, store_chunks
from rag_ingestion.stages.summary import generate_summary
from rag_ingestion.utils.counters import PipelineCounters
from rag_ingestion.utils.logger import log_skip, skipped_files
from rag_ingestion.utils.state import (
    build_file_signature,
    is_file_unchanged,
    load_ingestion_state,
    save_ingestion_state,
)


def main() -> None:
    """Parse CLI arguments and run the ingestion pipeline."""
    args = _parse_args()
    run_pipeline(args.source, collection_name=args.collection)


def run_pipeline(
    source: str,
    collection_name: str | None = None,
    enable_chunk_descriptions: bool | None = None,
    provider_config: dict | None = None,
    event_callback=None,
) -> PipelineCounters:
    """Run all ingestion stages in order."""
    counters = PipelineCounters()

    def emit(stage, message, level="info", progress=None, total=None, metadata=None):
        if event_callback:
            event_callback(
                stage=stage, message=message, level=level,
                progress=progress, total=total, metadata=metadata,
            )

    repository = load_repository(source)
    selected_collection = collection_name or expected_collection_name(
        repository["repository_root"]
    )
    validate_collection_binding(selected_collection, repository["repository_root"])

    # --- Discovery ---
    discovered_files = discover_files(repository["repository_root"], counters)
    emit("discovery", f"Discovered {counters.files_discovered} files in the repository.",
         progress=counters.files_discovered)

    # --- Filtering ---
    filtered_files = filter_files(
        discovered_files,
        repository["repository_root"],
        counters,
    )
    emit("filtering", f"Filtered repository — ignored {counters.files_ignored} generated or noise files.",
         progress=len(filtered_files))

    # --- Language detection ---
    language_files = detect_languages(filtered_files, counters)
    processable = [f for f in language_files if not f.skipped]
    emit("language",
         f"Detected supported languages for {len(processable)} files. "
         f"{counters.files_skipped_unsupported} files skipped as unsupported.",
         progress=len(processable))

    # --- Incremental state ---
    previous_state: dict[str, dict[str, int]] = {}
    next_state: dict[str, dict[str, int]] = {}
    modified_paths: list[str] = []  # files that were re-parsed (changed) in incremental mode
    if ENABLE_INCREMENTAL_FILE_SKIP:
        previous_state = load_ingestion_state(repository["repository_root"])

    # --- Parse + chunk ---
    all_chunks = []
    parsed_count = 0
    for file in language_files:
        if file.skipped:
            continue
        signature = build_file_signature(file)
        file_was_unchanged = ENABLE_INCREMENTAL_FILE_SKIP and is_file_unchanged(
            file.relative_path, signature, previous_state
        )
        if file_was_unchanged:
            next_state[file.relative_path] = signature
            if not is_repo_summary_evidence_path(file.relative_path):
                log_skip(file.relative_path, "unchanged_file", "skipped")
                continue
            log_skip(file.relative_path, "repo_summary_evidence_refresh", "parsed")

        # Only track as modified if the file actually changed (not a forced evidence refresh)
        if (ENABLE_INCREMENTAL_FILE_SKIP and not RECREATE_COLLECTION_EACH_RUN
                and not file_was_unchanged
                and file.relative_path in previous_state):
            modified_paths.append(file.relative_path)

        parsed = parse_file(file, counters)
        chunks = generate_chunks(parsed, file)
        chunks = handle_overflow(chunks)

        for chunk in chunks:
            build_metadata(chunk)
            chunk.summary = generate_summary(chunk)

        counters.chunks_generated += len(chunks)
        all_chunks.extend(chunks)
        if ENABLE_INCREMENTAL_FILE_SKIP:
            next_state[file.relative_path] = signature


        parsed_count += 1
        if parsed_count % 10 == 0:
            emit("parser", f"Parsed {parsed_count} files so far…",
                 progress=parsed_count, total=len(processable))

    emit("parser", f"Parsed {counters.files_parsed_ok} files successfully.",
         progress=counters.files_parsed_ok, total=len(processable), level="success")

    # --- Repo summary ---
    repo_summary = build_repo_summary_chunk(all_chunks, repository)
    if repo_summary is not None:
        build_metadata(repo_summary)
        all_chunks.append(repo_summary)
        counters.chunks_generated += 1

    emit("chunker", f"Generated {counters.chunks_generated} searchable chunks.",
         progress=counters.chunks_generated, total=counters.chunks_generated)

    if all_chunks:
        # --- Descriptions ---
        from rag_ingestion.stages.description import describe_chunks
        all_chunks = describe_chunks(
            all_chunks,
            enabled=enable_chunk_descriptions,
            provider_config=provider_config,
            event_callback=event_callback,
        )

        # --- Embedding ---
        emit("embedding", f"Embedding {len(all_chunks)} chunks…")
        embedded_chunks = embed_chunks(all_chunks, counters)
        emit("embedding",
             f"Generated embeddings for {counters.embeddings_generated} chunks.",
             level="success", progress=counters.embeddings_generated,
             total=len(all_chunks))

        # --- Storage: delete stale chunks for modified files first ---
        if modified_paths:
            emit("storage", f"Deleting stale chunks for {len(modified_paths)} modified file(s)…")
            delete_chunks_for_paths(modified_paths, collection_name=selected_collection)

        emit("storage", f"Storing {len(embedded_chunks)} chunks in Qdrant…")
        store_chunks(embedded_chunks, counters, collection_name=selected_collection)
        emit("storage",
             f"Stored {counters.embeddings_stored} chunks in Qdrant.",
             level="success", progress=counters.embeddings_stored,
             total=counters.embeddings_stored)

    if ENABLE_INCREMENTAL_FILE_SKIP:
        if not RECREATE_COLLECTION_EACH_RUN:
            removed_paths = sorted(set(previous_state) - set(next_state))
            if removed_paths:
                emit("storage", f"Deleting chunks for {len(removed_paths)} removed file(s)…")
                delete_chunks_for_paths(removed_paths, collection_name=selected_collection)
                emit("storage",
                     f"Deleted chunks for {len(removed_paths)} removed file(s).",
                     level="success")
        save_ingestion_state(repository["repository_root"], next_state)

    _print_report(repository, counters, collection_name=selected_collection or COLLECTION_NAME)
    return counters


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a local or public GitHub repository into Qdrant."
    )
    parser.add_argument(
        "source",
        help="Absolute local repository path or public GitHub URL.",
    )
    parser.add_argument(
        "--collection",
        default="",
        help="Optional Qdrant collection override for this run.",
    )
    return parser.parse_args()


def _print_report(repository: dict, counters: PipelineCounters, collection_name: str) -> None:
    print("========================================")
    print("Ingestion Complete")
    print("========================================")
    print(f"Repository:          {repository['repository_name']}")
    print(f"Source:              {repository['source_type']}")
    print()
    print(f"Files discovered:    {counters.files_discovered}")
    print(f"Files ignored:       {counters.files_ignored}")
    print(
        "Files skipped (unsupported language): "
        f"{counters.files_skipped_unsupported}"
    )
    print(f"Files parsed OK:     {counters.files_parsed_ok}")
    print(
        "Files parse failed:  "
        f"{counters.files_parse_failed} (fell back to file-level chunk)"
    )
    print()
    print(f"Chunks generated:    {counters.chunks_generated}")
    print(f"Embeddings stored:   {counters.embeddings_stored}")
    print()
    print(f"Collection:          {collection_name}")
    print("========================================")

    if skipped_files:
        print()
        print("Skipped files:")
        for item in skipped_files:
            print(f"- {item['file']} | {item['reason']} | {item['action']}")


if __name__ == "__main__":
    main()
