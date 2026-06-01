"""Configuration constants for the local ingestion pipeline."""

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION_NAME = "repository_chunks"

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

MAX_CHUNK_TOKENS = 2048
BATCH_SIZE = 128

SLIDING_WINDOW_SIZE = 100
SLIDING_OVERLAP = 20

TEMP_CLONE_DIR = "/tmp/rag_ingestion"
