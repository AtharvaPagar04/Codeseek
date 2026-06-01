"""Configuration for retrieval pipeline."""

from pathlib import Path

COLLECTION_NAME = "repository_chunks"
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
QUERY_PREFIX = "query: "

TOP_K_DENSE = 15
TOP_K_AFTER_MERGE = 10
MAX_CONTEXT_TOKENS = 7000
MAX_RESPONSE_TOKENS = 1024

EXPAND_CALLS = True
EXPAND_PARENT = True
EXPAND_SIBLINGS = False
EXPAND_SPLIT_PARTS = True
CALL_EXPANSION_LIMIT = 5

CONVERSATION_HISTORY_TURNS = 5
FILE_CACHE_MAX_SIZE = 128

# Must point to the same repository that was ingested.
REPO_ROOT = str(Path.cwd())

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_API_KEY_ENV = "GROQ_API_KEY"
