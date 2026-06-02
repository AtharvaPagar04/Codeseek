"""Configuration for retrieval pipeline."""

import os
from pathlib import Path

from retrieval.isolation import expected_collection_name

def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "repository_chunks")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = _env_int("QDRANT_PORT", 6333)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
QUERY_PREFIX = "query: "

TOP_K_DENSE = _env_int("RETRIEVAL_TOP_K_DENSE", 15)
TOP_K_AFTER_MERGE = _env_int("RETRIEVAL_TOP_K_AFTER_MERGE", 10)
MAX_CONTEXT_TOKENS = _env_int("RETRIEVAL_MAX_CONTEXT_TOKENS", 7000)
MAX_RESPONSE_TOKENS = _env_int("RETRIEVAL_MAX_RESPONSE_TOKENS", 1024)

EXPAND_CALLS = _env_bool("RETRIEVAL_EXPAND_CALLS", True)
EXPAND_PARENT = _env_bool("RETRIEVAL_EXPAND_PARENT", True)
EXPAND_SIBLINGS = _env_bool("RETRIEVAL_EXPAND_SIBLINGS", False)
EXPAND_SPLIT_PARTS = _env_bool("RETRIEVAL_EXPAND_SPLIT_PARTS", True)
CALL_EXPANSION_LIMIT = _env_int("RETRIEVAL_CALL_EXPANSION_LIMIT", 5)

CONVERSATION_HISTORY_TURNS = 5
FILE_CACHE_MAX_SIZE = 128

# Must point to the same repository that was ingested.
REPO_ROOT = os.getenv("RETRIEVAL_REPO_ROOT", str(Path.cwd()))


def get_collection_name() -> str:
    """Read collection name at runtime to support multi-repo sessions."""
    explicit = os.getenv("QDRANT_COLLECTION_NAME", "").strip()
    if explicit:
        return explicit
    return expected_collection_name(get_repo_root())


def get_repo_root() -> str:
    """Read repo root at runtime to support multi-repo sessions."""
    return os.getenv("RETRIEVAL_REPO_ROOT", REPO_ROOT)

GROQ_MODEL = os.getenv("RETRIEVAL_GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_KEY_ENV = "GROQ_API_KEY"

# Reliability tuning
RETRIEVAL_QDRANT_TIMEOUT_SECONDS = float(
    os.getenv("RETRIEVAL_QDRANT_TIMEOUT_SECONDS", "5.0")
)
RETRIEVAL_GROQ_TIMEOUT_SECONDS = float(
    os.getenv("RETRIEVAL_GROQ_TIMEOUT_SECONDS", "20.0")
)
RETRIEVAL_RETRY_ATTEMPTS = _env_int("RETRIEVAL_RETRY_ATTEMPTS", 3)
RETRIEVAL_RETRY_BACKOFF_SECONDS = float(
    os.getenv("RETRIEVAL_RETRY_BACKOFF_SECONDS", "0.5")
)
RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD = _env_int(
    "RETRIEVAL_CIRCUIT_BREAKER_THRESHOLD", 3
)
RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS = float(
    os.getenv("RETRIEVAL_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30.0")
)
