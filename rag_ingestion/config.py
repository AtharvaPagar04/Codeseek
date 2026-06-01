"""Configuration constants for the local ingestion pipeline."""

import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "repository_chunks")
RECREATE_COLLECTION_EACH_RUN = _env_bool("QDRANT_RECREATE_COLLECTION", False)
ENABLE_INCREMENTAL_FILE_SKIP = _env_bool("INGESTION_ENABLE_INCREMENTAL_FILE_SKIP", True)
INGESTION_STATE_FILENAME = ".rag_ingestion_state.json"

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

MAX_CHUNK_TOKENS = 2048
BATCH_SIZE = 128

SLIDING_WINDOW_SIZE = 100
SLIDING_OVERLAP = 20

TEMP_CLONE_DIR = "/tmp/rag_ingestion"
