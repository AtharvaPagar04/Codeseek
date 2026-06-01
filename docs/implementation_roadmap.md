# RAG Ingestion Pipeline — Implementation Roadmap

---

## Phase 1: Project Setup

1. Create the folder structure as defined in the architecture doc:
   - `rag_ingestion/main.py`, `config.py`
   - `stages/`, `models/`, `utils/` with `__init__.py` in each

2. Install all dependencies:
   ```
   pip install qdrant-client tree-sitter tree-sitter-python tree-sitter-javascript \
               sentence-transformers tiktoken pathspec gitpython requests
   ```

3. Start Qdrant locally:
   ```
   docker run -p 6333:6333 qdrant/qdrant
   ```

4. Write `config.py` — all constants go here, nothing hardcoded elsewhere:
   - QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME
   - EMBEDDING_MODEL, EMBEDDING_DIM
   - MAX_CHUNK_TOKENS, BATCH_SIZE
   - SLIDING_WINDOW_SIZE, SLIDING_OVERLAP
   - TEMP_CLONE_DIR

---

## Phase 2: Models

Write all three dataclasses first. These are plain data containers with no logic.

5. `models/file.py` — FileRecord dataclass
   - Fields: path, relative_path, extension, size_bytes, language, skipped, skip_reason

6. `models/parsed.py` — ParsedSymbol and ParsedFile dataclasses
   - ParsedSymbol: symbol_name, symbol_type, parent_symbol, start_line, end_line, parameters, methods, docstring, calls
   - ParsedFile: relative_path, language, parse_status, imports, symbols

7. `models/chunk.py` — Chunk dataclass
   - All fields including parameters, methods, file_symbols (newly added)
   - summary and embedding fields default to empty (filled later by their respective stages)

---

## Phase 3: Utilities

8. `utils/logger.py` — single function `log_skip(file, reason, action)`
   - Appends to a module-level list; main.py reads it at the end

9. `utils/counters.py` — PipelineCounters dataclass
   - All counter fields default to 0

---

## Phase 4: Stages (implement in pipeline order)

Each stage is a single file with one public function. No stage imports from another stage.

10. `stages/loader.py` — `load_repository(source: str) -> dict`
    - Handle local path (verify exists) and GitHub URL (clone with gitpython)
    - For private GitHub repos, allow token auth via `GITHUB_TOKEN` or `GH_TOKEN`
    - Return dict with repository_name, repository_root, source_type

11. `stages/discovery.py` — `discover_files(repository_root, counters) -> list[FileRecord]`
    - os.walk from root, build a FileRecord for every file
    - Increment counters.files_discovered

12. `stages/filtering.py` — `filter_files(files, repo_root, counters) -> list[FileRecord]`
    - Pass 1: apply .gitignore rules using pathspec
    - Pass 2: apply hardcoded system ignore rules (dirs, extensions, patterns)
    - Increment counters.files_ignored for every removed file

13. `stages/language.py` — `detect_languages(files, counters) -> list[FileRecord]`
    - Map extension to language using LANGUAGE_MAP constant
    - Mark unsupported files with skipped=True, log via logger, increment counter
    - Note: .jsx and .tsx both map to javascript/typescript here but use different grammars in parser.py

14. `stages/parser.py` — `parse_file(file, counters) -> ParsedFile`
    - Select Tree-Sitter grammar by file.extension (not file.language)
      - .tsx → language_tsx() from tree-sitter-typescript
      - .ts  → language_typescript()
      - .jsx/.js → JSX-enabled JS grammar
      - .py → Python grammar
    - Walk AST, build ParsedSymbol for every function/class/method
    - Extract: parameters, methods, docstring, calls, imports
    - On failure: log, return ParsedFile with parse_status="failed", increment counter

15. `stages/chunker.py` — `generate_chunks(parsed: ParsedFile, file: FileRecord) -> list[Chunk]`
    - If parse failed: return one file-level Chunk with full content
    - Otherwise: one Chunk per ParsedSymbol
    - Copy parameters, methods from ParsedSymbol onto Chunk
    - Populate file_symbols only for file-level chunks

16. `stages/overflow.py` — `handle_overflow(chunks) -> list[Chunk]`
    - Count tokens per chunk using tiktoken cl100k_base
    - If over MAX_CHUNK_TOKENS: split with sliding window (100 lines, 20 overlap)
    - Set chunk_part and total_parts on every chunk

17. `stages/metadata.py` — `build_metadata(chunk) -> Chunk`
    - Build deterministic chunk_id using sha256 of relative_path + parent_symbol + symbol_name + chunk_part
    - Count tokens and set chunk.token_count
    - parent_symbol must be in the hash to avoid ID collisions between same-named methods in different classes

18. `stages/summary.py` — `generate_summary(chunk) -> str`
    - No LLM. Use AST data already on the Chunk.
    - function:  "Function: name\nParameters: ...\nDocstring: ..."
    - method:    "Method: name\nClass: parent\nParameters: ..."
    - class:     "Class: name\nMethods: ...\nDocstring: ..."
    - file:      "File: path\nSymbols: ..."

19. `stages/embedder.py` — `embed_chunks(chunks, counters) -> list[Chunk]`
    - Load SentenceTransformer once at module level (not inside the function)
    - Build structured input string per chunk (File + Language + Type + Symbol + Summary + Docstring + Code)
    - Batch embed in groups of BATCH_SIZE using model.encode()
    - Increment counters.embeddings_generated

20. `stages/storage.py` — `store_chunks(chunks, counters) -> None`
    - Connect to Qdrant, recreate_collection on every run
    - Build PointStruct per chunk: sequential int id, embedding as vector, all metadata as payload
    - Do NOT store content or embedding in payload
    - Must include relative_path in payload
    - Increment counters.embeddings_stored

---

## Phase 5: Entry Point

21. `main.py` — wire all stages together in order
    - Parse CLI arg (local path or GitHub URL)
    - Call stages in sequence, pass output of each as input to next
    - For file-level loop: parser → chunker → overflow → metadata (map) → summary (map)
    - After all files: embedder → storage
    - Print final report with all counters and skip log

---

## Phase 6: Smoke Test

22. After a successful run, verify in Python:
    ```python
    from qdrant_client import QdrantClient
    client = QdrantClient("localhost", port=6333)
    info = client.get_collection("repository_chunks")
    print(info.points_count)  # should match embeddings_stored
    ```

23. Run a dummy search to confirm the collection is queryable:
    ```python
    results = client.search(
        collection_name="repository_chunks",
        query_vector=[0.0] * 384,
        limit=3
    )
    for r in results:
        print(r.payload["symbol_name"], r.payload["relative_path"])
    ```

---

## Known Gaps to Fix Before Production

- No true incremental reindexing policy (mtime/hash change detection is still missing)
- Content is not stored in Qdrant (must re-read from disk at retrieval time)
- calls field is extracted but not yet used in retrieval
- No auth or rate limiting
