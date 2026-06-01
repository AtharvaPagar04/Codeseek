# RAG Ingestion Pipeline — Local Development Documentation

---

## What This Is

A local ingestion pipeline that takes a code repository (local folder or GitHub URL),
parses the code into meaningful chunks, generates embeddings, and stores them in Qdrant.

This is ingestion only. No retrieval, no chat, no agents. Just getting data into the vector store
in a form that is actually useful.

---

## System Requirements

Python 3.10+
Qdrant running locally on port 6333 (Docker recommended)
Tree-Sitter
sentence-transformers
pathspec
tiktoken

Install everything:

    pip install qdrant-client tree-sitter tree-sitter-python tree-sitter-javascript \
                sentence-transformers tiktoken pathspec gitpython requests

Run Qdrant locally:

    docker run -p 6333:6333 qdrant/qdrant

---

## High Level Flow

    Repository (local path or GitHub URL)
        |
        v
    Repository Loader         -- loads repo to local disk
        |
        v
    File Discovery            -- recursive walk, collect all files
        |
        v
    File Filtering            -- remove noise (gitignore, node_modules, binaries, etc.)
        |
        v
    Language Detection        -- determine file language, skip unsupported
        |
        v
    Code Parsing              -- Tree-Sitter AST extraction
        |
        v
    Chunk Generation          -- split into function / class / file chunks
        |
        v
    Overflow Handling         -- split oversized chunks with sliding window
        |
        v
    Metadata Generation       -- build full metadata object per chunk
        |
        v
    Summary Generation        -- AST-based summaries, no LLM
        |
        v
    Embedding Generation      -- batch embed with BAAI/bge-small-en-v1.5
        |
        v
    Qdrant Storage            -- upsert chunks into collection
        |
        v
    Final Report              -- print counts to stdout

---

## Pipeline Counters

Track these across the entire run. Increment as pipeline progresses. Print at the end.

    {
      "files_discovered": 0,
      "files_ignored_by_filter": 0,
      "files_skipped_unsupported_language": 0,
      "files_parsed": 0,
      "files_parse_failed": 0,
      "chunks_generated": 0,
      "embeddings_generated": 0,
      "embeddings_stored": 0
    }

These are the only numbers you should trust at the end of a run.

---

## Stage 1: Repository Loader

### Responsibility

Get the repository onto local disk and return a root path to work from.

### Input

Either:
    - Absolute local path: /home/user/projects/myrepo
    - GitHub URL: https://github.com/user/project

### Behavior

For local path:
    - Verify the path exists and is a directory
    - Use as-is, no copying

For GitHub URL:
    - Clone using git clone into a temp directory under /tmp/rag_ingestion/
    - Public repos work directly
    - Private repos are supported when GITHUB_TOKEN or GH_TOKEN is set
    - If clone fails, exit with error

### Output

    {
      "repository_name": "myrepo",
      "repository_root": "/absolute/path/to/repo",
      "source_type": "local" | "github"
    }

---

## Stage 2: File Discovery

### Responsibility

Walk the entire repository tree and collect all files.

### Behavior

Use os.walk() starting from repository_root.
For every file found, record:

    {
      "path": "/absolute/path/to/file.py",
      "relative_path": "src/auth.py",
      "extension": ".py",
      "size_bytes": 4096
    }

relative_path is computed as path relative to repository_root. This is what gets stored
and displayed later. Never store absolute paths in metadata shown to users.

### Output

List of file descriptor objects. No filtering yet at this stage.

---

## Stage 3: File Filtering

### Responsibility

Remove files that should not be ingested. Two stages.

---

### Stage 3a: .gitignore Rules

If a .gitignore file exists at the repository root, parse it using pathspec:

    import pathspec

    with open(".gitignore") as f:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", f)

    # Filter out any file whose relative_path matches
    filtered = [f for f in files if not spec.match_file(f["relative_path"])]

---

### Stage 3b: System Ignore Rules

Always apply these regardless of .gitignore.

Ignore directories (skip entire subtree if path contains these):

    .git
    .github
    node_modules
    .next
    dist
    build
    coverage
    venv
    .venv
    __pycache__
    .mypy_cache
    .pytest_cache

Ignore specific filenames:

    package-lock.json
    yarn.lock
    pnpm-lock.yaml
    Cargo.lock
    poetry.lock
    Gemfile.lock

Ignore by extension (binary and media):

    .png .jpg .jpeg .webp .gif .ico
    .pdf .svg
    .zip .rar .tar .gz .7z
    .exe .dll .so .dylib
    .pyc .pyo

Ignore minified files:

    *.min.js
    *.min.css

Ignore generated files (by pattern):

    *_generated.py
    *_pb2.py
    *.pb.go
    generated/
    gen/

Ignore environment and OS files:

    .env
    .env.local
    .env.production
    .env.development
    .DS_Store
    Thumbs.db

### Output

Filtered list of files approved for processing.
Increment files_ignored_by_filter counter for every file removed here.

---

## Stage 4: Language Detection

### Responsibility

Determine the programming language for each file.

### Supported Languages (V1)

    .py   → python
    .js   → javascript
    .jsx  → javascript  (NOTE: requires JSX-enabled grammar, see Parsing stage)
    .ts   → typescript
    .tsx  → typescript  (NOTE: requires tree-sitter-tsx grammar specifically)

### Behavior

Look up the file extension in the mapping above.
If extension is not in the map, mark file as unsupported.

For unsupported files:
    - Do NOT process
    - Log to a skipped_files list:
        {
          "file": "main.go",
          "reason": "unsupported_language",
          "action": "skipped"
        }
    - Increment files_skipped_unsupported_language counter

### Output

Each file now has a language field, or is removed from the processing list with a log entry.

---

## Stage 5: Code Parsing

### Responsibility

Parse each file using Tree-Sitter and extract symbols.

### Tree-Sitter Setup

    from tree_sitter import Language, Parser
    import tree_sitter_python
    import tree_sitter_javascript

    PY_LANGUAGE = Language(tree_sitter_python.language())
    JS_LANGUAGE = Language(tree_sitter_javascript.language())

For TypeScript use tree-sitter-typescript.
For TSX specifically use the tsx() export from tree-sitter-typescript, not typescript().
For JSX use tree-sitter-javascript with jsx=True if available, or the dedicated grammar.

This matters. Using the wrong grammar on a JSX/TSX file will cause silent parse failures
or miss JSX syntax entirely.

### What to Extract

For each file, extract all of these using AST traversal:

Functions:
    - symbol_name
    - symbol_type = "function"
    - start_line
    - end_line
    - parameters (list of parameter names)
    - docstring (first string literal inside body if present)

Classes:
    - symbol_name
    - symbol_type = "class"
    - start_line
    - end_line
    - methods (list of method names)
    - docstring

Methods (functions inside a class):
    - symbol_name
    - symbol_type = "method"
    - parent_symbol (name of the containing class)
    - start_line
    - end_line
    - parameters
    - docstring

Imports:
    - List of imported module/symbol strings
    - Collected at file level, not per-symbol

Calls (collect but optional to use in V1):
    - List of function/method names called within each symbol
    - Worth extracting now even if not used yet
    - Enables dependency tracing later without re-ingestion

### Parse Failure Handling

Tree-Sitter can fail on malformed files, unusual encodings, or edge-case syntax.
Do not let a parse failure crash the pipeline.

On failure:

    1. Log:
        {
          "file": "relative_path",
          "reason": "ast_parse_failed",
          "action": "file_level_fallback"
        }
    2. Increment files_parse_failed counter
    3. Fall back: treat the entire file as one chunk (file-level chunk)
    4. Do not attempt symbol extraction for this file

### Output

Per file:

    {
      "file_path": "...",
      "relative_path": "...",
      "language": "python",
      "parse_status": "ok" | "failed",
      "imports": [],
      "symbols": [
        {
          "symbol_name": "verify_token",
          "symbol_type": "function",
          "parent_symbol": null,
          "start_line": 10,
          "end_line": 25,
          "parameters": ["token"],
          "docstring": "Validate JWT token.",
          "calls": ["jwt.decode", "raise_exception"]
        }
      ]
    }

---

## Stage 6: Chunk Generation

### Responsibility

Convert parsed symbols into chunks. One chunk per symbol where possible.

### Chunk Types

function  -- preferred, one function = one chunk
class     -- entire class body as one chunk (use when class has no methods parsed separately)
method    -- one method = one chunk (preferred over class-level chunk)
file      -- fallback, entire file as one chunk

### Rules

1. Never split in the middle of a function body.
2. Never split in the middle of a class body.
3. Respect AST boundaries. start_line and end_line come from Tree-Sitter.
4. One symbol = one chunk unless it exceeds max_chunk_tokens.

### Chunk Content

The content field of a chunk is the raw source code lines from start_line to end_line,
extracted directly from the file. Read the file, slice lines, join as string.

### Output

List of raw chunks before overflow handling. Each chunk has:
    symbol reference, content, line range, language, file reference.

---

## Stage 7: Overflow Handling

### Responsibility

Split chunks that exceed the token limit.

### Configuration

    MAX_CHUNK_TOKENS = 2048

Use tiktoken to count tokens:

    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    token_count = len(enc.encode(content))

### Strategy

If token_count <= MAX_CHUNK_TOKENS: chunk passes through unchanged.
    chunk_part = 1, total_parts = 1

If token_count > MAX_CHUNK_TOKENS: apply sliding window split.

Sliding window:
    - Window size: 100 lines
    - Overlap: 20 lines
    - Split the content into overlapping windows
    - Each window becomes a separate chunk with its own chunk_part index

Example for a 260-line function with window=100, overlap=20:

    Part 1: lines 1-100
    Part 2: lines 81-180
    Part 3: lines 161-260

Set total_parts = number of windows generated.
Set chunk_part = 1-indexed position.

If the chunk came from a parse failure fallback (whole file), apply same logic.

---

## Stage 8: Metadata Generation

### Responsibility

Build the final metadata object for every chunk.

### chunk_id Generation

chunk_id must be deterministic so the same chunk always gets the same ID.
This is required for upsert behavior in Qdrant (re-running ingestion should not duplicate).

Strategy:

    import hashlib

    raw = f"{relative_path}::{symbol_name}::{chunk_part}"
    chunk_id = hashlib.sha256(raw.encode()).hexdigest()[:32]

For file-level chunks where there is no symbol_name, use:

    raw = f"{relative_path}::__file__::{chunk_part}"

Do not use content hash. Code changes, path+symbol+part does not change for the same logical chunk.

### Full Metadata Schema

    {
      "chunk_id": "a3f9...",          # deterministic hash
      "file_path": "/abs/path/auth.py",
      "relative_path": "src/auth.py",
      "language": "python",
      "chunk_type": "function",       # function | class | method | file
      "symbol_name": "verify_token",
      "parent_symbol": null,          # set if method inside class
      "start_line": 10,
      "end_line": 25,
      "chunk_part": 1,
      "total_parts": 1,
      "token_count": 128,
      "imports": ["jwt", "datetime"],
      "calls": ["jwt.decode"],        # extracted at parse time
      "docstring": "Validate JWT token.",
      "summary": "",                  # filled in next stage
      "content": "def verify_token(token):\n    ..."
    }

---

## Stage 9: Summary Generation

### Responsibility

Generate a short human-readable description of each chunk. No LLM. AST data only.

### Function Summary

Input symbol: verify_token, type: function, parameters: [token]

    Function: verify_token
    Parameters: token

If docstring exists:

    Function: verify_token
    Parameters: token
    Docstring: Validate JWT token.

### Method Summary

Input symbol: create_user, type: method, parent: UserService, parameters: [username, email]

    Method: create_user
    Class: UserService
    Parameters: username, email

### Class Summary

Input symbol: UserService, type: class, methods: [create_user, update_user, delete_user]

    Class: UserService
    Methods: create_user, update_user, delete_user

### File-Level Summary

Input: file auth.py, symbols: [verify_token, create_token, refresh_token]

    File: auth.py
    Symbols: verify_token, create_token, refresh_token

Write this string into the summary field of the metadata object.

---

## Stage 10: Embedding Generation

### Responsibility

Convert each chunk into a vector.

### Model

    BAAI/bge-small-en-v1.5

Load once at startup:

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")

Output dimension: 384

### Embedding Input

Do not embed just the raw code. Build a structured input string:

    File: {relative_path}
    Language: {language}
    Type: {chunk_type}
    Symbol: {symbol_name}
    Summary: {summary}
    Docstring: {docstring}
    Code:
    {content}

### Batching

    BATCH_SIZE = 128

Collect chunks into batches of 128, embed each batch in one call:

    embeddings = model.encode(batch_inputs, batch_size=128, show_progress_bar=True)

Do not embed one chunk at a time. It will be very slow.

### Output

Each chunk gets an embedding field: list of 384 floats.

---

## Stage 11: Qdrant Storage

### Responsibility

Store chunks and their embeddings in Qdrant.

### Collection Setup

Collection name: repository_chunks

Create if not exists:

    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    client = QdrantClient("localhost", port=6333)

    client.recreate_collection(
        collection_name="repository_chunks",
        vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    )

For local use, recreate_collection on every run is fine.
Later when you want incremental reindexing, switch to create_collection + upsert.

### HNSW Configuration

For local use, defaults are fine. Do not tune HNSW yet.

### Stored Payload

Store this in the Qdrant payload (not the vector):

    {
      "chunk_id": "...",
      "file_path": "...",
      "relative_path": "...",       # include this, not just file_path
      "language": "...",
      "chunk_type": "...",
      "symbol_name": "...",
      "parent_symbol": "...",
      "start_line": 0,
      "end_line": 0,
      "chunk_part": 1,
      "total_parts": 1,
      "imports": [],
      "calls": [],
      "docstring": "...",
      "summary": "..."
    }

Do not store the content or embedding in the payload. Content is large.
If you need content at retrieval time, re-read from disk using relative_path + line range.

### Upsert

    from qdrant_client.models import PointStruct

    points = [
        PointStruct(
            id=i,                        # sequential int for local use
            vector=chunk["embedding"],
            payload=chunk["payload"]
        )
        for i, chunk in enumerate(chunks)
    ]

    client.upsert(collection_name="repository_chunks", points=points)

For local use, sequential int IDs are fine. For deterministic upserts later,
convert chunk_id hash to a UUID or use the hash string directly as the point ID.

---

## Final Report

Print to stdout at end of run:

    ========================================
    Ingestion Complete
    ========================================
    Repository:          myrepo
    Source:              local

    Files discovered:    842
    Files ignored:       391
    Files skipped (unsupported language): 18
    Files parsed OK:     428
    Files parse failed:  5 (fell back to file-level chunk)

    Chunks generated:    2,174
    Embeddings stored:   2,174

    Collection:          repository_chunks
    ========================================

---

## Known Gaps (Fix Before Production, Fine For Local)

1. No incremental reindexing. Every run recreates the collection from scratch.
   Fix: check file mtime, skip unchanged files, upsert by chunk_id.

2. Private GitHub repos require credentials in environment variables:
   - GITHUB_TOKEN or GH_TOKEN for HTTPS cloning
   - Missing credentials will fail clone for private repositories

3. JSX/TSX grammar: if you see parse failures on .jsx or .tsx files, you likely have
   the wrong Tree-Sitter grammar loaded. Use tree-sitter-tsx for .tsx explicitly.

4. Content not stored in Qdrant. At retrieval time you need to re-read from disk.
   For local use this is fine as long as the repo is still on disk.

5. calls field is extracted but not used in retrieval or search yet.
   Keep extracting it. It enables impact analysis later.

6. No authentication or rate limiting. Fine for local single-user use.

---

## File Structure Suggestion

    ingestion/
        main.py               # entry point, wires stages together, prints report
        loader.py             # Stage 1: repo loader
        discovery.py          # Stage 2: file walker
        filtering.py          # Stage 3: gitignore + system rules
        language.py           # Stage 4: extension to language mapping
        parser.py             # Stage 5: tree-sitter parsing
        chunker.py            # Stage 6+7: chunk generation + overflow
        metadata.py           # Stage 8: metadata assembly + chunk_id
        summary.py            # Stage 9: AST-based summaries
        embedder.py           # Stage 10: batch embedding
        storage.py            # Stage 11: qdrant upsert
        config.py             # MAX_CHUNK_TOKENS, BATCH_SIZE, collection name, etc.

Keep each stage as a function or class that takes a well-defined input and returns
a well-defined output. Wire them together in main.py. This makes each stage
testable and replaceable independently.

---

## Quick Smoke Test

After a successful run:

    from qdrant_client import QdrantClient

    client = QdrantClient("localhost", port=6333)
    info = client.get_collection("repository_chunks")
    print(info.points_count)   # should match embeddings_stored from report

    results = client.search(
        collection_name="repository_chunks",
        query_vector=[0.0] * 384,   # dummy vector
        limit=3
    )
    for r in results:
        print(r.payload["symbol_name"], r.payload["relative_path"])

If points_count matches and search returns results without error, ingestion worked.
