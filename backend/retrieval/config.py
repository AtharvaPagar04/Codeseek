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
TOP_K_LEXICAL = _env_int("RETRIEVAL_TOP_K_LEXICAL", 15)
TOP_K_AFTER_MERGE = _env_int("RETRIEVAL_TOP_K_AFTER_MERGE", 10)
MAX_CONTEXT_TOKENS = _env_int("RETRIEVAL_MAX_CONTEXT_TOKENS", 7000)
MAX_RESPONSE_TOKENS = _env_int("RETRIEVAL_MAX_RESPONSE_TOKENS", 1024)

ENABLE_LEXICAL_RETRIEVAL = _env_bool("RETRIEVAL_ENABLE_LEXICAL", False)
ENABLE_DENSE_RETRIEVAL = _env_bool("RETRIEVAL_ENABLE_DENSE", True)
ENABLE_SCORED_INTENT = _env_bool("RETRIEVAL_ENABLE_SCORED_INTENT", True)
# Two-layer source gating: display_sources (strict, cited) vs reasoning_sources (broader, synthesis-only).
# Disable to fall back to single-list behaviour where all assembled sources are both cited and reasoned from.
ENABLE_TWO_LAYER_SOURCES = _env_bool("RETRIEVAL_ENABLE_TWO_LAYER_SOURCES", True)

# Display and reasoning source caps (plan §Source Set Size Decision).
DISPLAY_SOURCES_CAP = _env_int("RETRIEVAL_DISPLAY_SOURCES_CAP", 6)
REASONING_SOURCES_CAP = _env_int("RETRIEVAL_REASONING_SOURCES_CAP", 12)

# Intent-aware context budgets (plan §Intent-Aware Context Budget Starting Values).
# Keyed by primary_intent string; fallback is MAX_CONTEXT_TOKENS.
INTENT_CONTEXT_BUDGETS: dict[str, int] = {
    "OVERVIEW":      5000,
    "TECH_STACK":    4500,
    "ARCHITECTURE":  6000,
    "SYMBOL":        2500,
    "FILE":          2500,
    "SEMANTIC":      5000,
    "TRACE":         6500,
    "DEPENDENCY":    6500,
    "FOLLOWUP":      4500,
    "EXPLANATION":   4500,
    "CODE_REQUEST":  5500,
    "CONFIG":        4000,
    "LOW_CONTEXT":   2500,
}

# History token caps — prevent conversation history from starving code context.
# HISTORY_TOKEN_CAP is a global hard ceiling regardless of intent.
# INTENT_HISTORY_CAPS further reduce the cap for broad/synthesis intents that
# need the most code context and are least dependent on exact prior answers.
HISTORY_TOKEN_CAP = _env_int("RETRIEVAL_HISTORY_TOKEN_CAP", 1500)
INTENT_HISTORY_CAPS: dict[str, int] = {
    "OVERVIEW":      800,
    "TECH_STACK":    800,
    "ARCHITECTURE":  1000,
    "TRACE":         1000,
    "DEPENDENCY":    1000,
    "SEMANTIC":      1200,
    "EXPLANATION":   1200,
    "FOLLOWUP":      1200,
    "CODE_REQUEST":  1500,
    "SYMBOL":        1500,
    "FILE":          1500,
    "CONFIG":        1500,
    "LOW_CONTEXT":   600,
}

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
