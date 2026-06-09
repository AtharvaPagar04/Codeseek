"""Configuration constants for the local ingestion pipeline."""

import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = _env_int("QDRANT_PORT", 6333)
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "repository_chunks")
RECREATE_COLLECTION_EACH_RUN = _env_bool("QDRANT_RECREATE_COLLECTION", False)

ENABLE_INCREMENTAL_FILE_SKIP = _env_bool(
    "INGESTION_ENABLE_INCREMENTAL_FILE_SKIP",
    True,
)
INGESTION_STATE_FILENAME = ".rag_ingestion_state.json"

EMBEDDING_MODEL = os.getenv("INGESTION_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = _env_int("INGESTION_EMBEDDING_DIM", 384)

MAX_CHUNK_TOKENS = _env_int("INGESTION_MAX_CHUNK_TOKENS", 2048)
BATCH_SIZE = _env_int("INGESTION_BATCH_SIZE", 128)

SLIDING_WINDOW_SIZE = _env_int("INGESTION_SLIDING_WINDOW_SIZE", 100)
SLIDING_OVERLAP = _env_int("INGESTION_SLIDING_OVERLAP", 20)

TEMP_CLONE_DIR = os.getenv("INGESTION_TEMP_CLONE_DIR", "/tmp/rag_ingestion")

ENABLE_LLM_CHUNK_DESCRIPTIONS = _env_bool(
    "ENABLE_LLM_CHUNK_DESCRIPTIONS",
    False,
)
CHUNK_DESCRIPTION_MAX_INPUT_CHARS = _env_int(
    "CHUNK_DESCRIPTION_MAX_INPUT_CHARS",
    1200,
)
CHUNK_DESCRIPTION_MAX_WORDS = _env_int(
    "CHUNK_DESCRIPTION_MAX_WORDS",
    80,
)
CHUNK_DESCRIPTION_MAX_CHUNKS = _env_int(
    "CHUNK_DESCRIPTION_MAX_CHUNKS",
    80,
)
CHUNK_DESCRIPTION_SLEEP_SECONDS = float(
    os.getenv("CHUNK_DESCRIPTION_SLEEP_SECONDS", "0")
)
CHUNK_DESCRIPTION_RETRY_ON_RATE_LIMIT = _env_bool(
    "CHUNK_DESCRIPTION_RETRY_ON_RATE_LIMIT",
    False,
)
CHUNK_DESCRIPTION_MAX_OUTPUT_TOKENS = _env_int(
    "CHUNK_DESCRIPTION_MAX_OUTPUT_TOKENS",
    60,
)

EMBEDDING_INPUT_MAX_CODE_CHARS = _env_int("EMBEDDING_INPUT_MAX_CODE_CHARS", 6000)
EMBEDDING_INPUT_MAX_TOTAL_CHARS = _env_int("EMBEDDING_INPUT_MAX_TOTAL_CHARS", 10000)

ENABLE_CHUNK_LABELS = _env_bool("ENABLE_CHUNK_LABELS", True)
ENABLE_LLM_LABEL_REFINEMENT = _env_bool("ENABLE_LLM_LABEL_REFINEMENT", False)
# Max chunks passed to LLM label refinement per indexing run.
# Kept small (20) for RTX 3050 / 4 GB VRAM safety; raise via env for larger machines.
CHUNK_LABEL_LLM_MAX_CHUNKS = _env_int("CHUNK_LABEL_LLM_MAX_CHUNKS", 20)
# Max characters of content excerpt included in the refinement prompt (no full code).
CHUNK_LABEL_LLM_MAX_CONTENT_CHARS = _env_int("CHUNK_LABEL_LLM_MAX_CONTENT_CHARS", 1200)
# Timeout (seconds) for a single LLM label refinement request.
CHUNK_LABEL_LLM_TIMEOUT_SECONDS = _env_int("CHUNK_LABEL_LLM_TIMEOUT_SECONDS", 30)

# ---------------------------------------------------------------------------
# GPU / VRAM cleanup after indexing stages
# ---------------------------------------------------------------------------
ENABLE_GPU_CLEANUP_AFTER_STAGES = _env_bool("ENABLE_GPU_CLEANUP_AFTER_STAGES", True)
UNLOAD_LOCAL_LLM_AFTER_INDEXING = _env_bool("UNLOAD_LOCAL_LLM_AFTER_INDEXING", True)
# Unload Ollama model immediately after description generation (before embedding).
# Defaults to False to avoid slow re-loads when descriptions run in batches.
UNLOAD_LOCAL_LLM_AFTER_DESCRIPTIONS = _env_bool("UNLOAD_LOCAL_LLM_AFTER_DESCRIPTIONS", False)
UNLOAD_EMBEDDING_MODEL_AFTER_INDEXING = _env_bool("UNLOAD_EMBEDDING_MODEL_AFTER_INDEXING", True)

# Embedding model device: "cpu" keeps the model off CUDA; "cuda" allows GPU embedding.
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")
# Batch size for encode() calls — smaller batches use less VRAM at a time.
EMBEDDING_BATCH_SIZE = _env_int("EMBEDDING_BATCH_SIZE", 4)

# Ollama model name to evict after indexing.  Defaults to the primary LLM model
# so the most common local setup works without extra configuration.
LOCAL_LLM_UNLOAD_MODEL = os.getenv(
    "LOCAL_LLM_UNLOAD_MODEL",
    os.getenv("RETRIEVAL_LOCAL_LLM_PRIMARY_MODEL", ""),
)