"""Entry point for the RAG ingestion pipeline."""

import argparse

from rag_ingestion.config import COLLECTION_NAME
from rag_ingestion.stages.chunker import generate_chunks
from rag_ingestion.stages.discovery import discover_files
from rag_ingestion.stages.embedder import embed_chunks
from rag_ingestion.stages.filtering import filter_files
from rag_ingestion.stages.language import detect_languages
from rag_ingestion.stages.loader import load_repository
from rag_ingestion.stages.metadata import build_metadata
from rag_ingestion.stages.overflow import handle_overflow
from rag_ingestion.stages.parser import parse_file
from rag_ingestion.stages.storage import store_chunks
from rag_ingestion.stages.summary import generate_summary
from rag_ingestion.utils.counters import PipelineCounters
from rag_ingestion.utils.logger import skipped_files


def main() -> None:
    """Parse CLI arguments and run the ingestion pipeline."""
    args = _parse_args()
    run_pipeline(args.source)


def run_pipeline(source: str) -> PipelineCounters:
    """Run all ingestion stages in order."""
    counters = PipelineCounters()

    repository = load_repository(source)
    discovered_files = discover_files(repository["repository_root"], counters)
    filtered_files = filter_files(
        discovered_files,
        repository["repository_root"],
        counters,
    )
    language_files = detect_languages(filtered_files, counters)

    all_chunks = []
    for file in language_files:
        if file.skipped:
            continue

        parsed = parse_file(file, counters)
        chunks = generate_chunks(parsed, file)
        chunks = handle_overflow(chunks)

        for chunk in chunks:
            build_metadata(chunk)
            chunk.summary = generate_summary(chunk)

        counters.chunks_generated += len(chunks)
        all_chunks.extend(chunks)

    embedded_chunks = embed_chunks(all_chunks, counters)
    store_chunks(embedded_chunks, counters)
    _print_report(repository, counters)
    return counters


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a local or public GitHub repository into Qdrant."
    )
    parser.add_argument(
        "source",
        help="Absolute local repository path or public GitHub URL.",
    )
    return parser.parse_args()


def _print_report(repository: dict, counters: PipelineCounters) -> None:
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
    print(f"Collection:          {COLLECTION_NAME}")
    print("========================================")

    if skipped_files:
        print()
        print("Skipped files:")
        for item in skipped_files:
            print(f"- {item['file']} | {item['reason']} | {item['action']}")


if __name__ == "__main__":
    main()
