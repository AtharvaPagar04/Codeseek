# RAG Retrieval Pipeline — Modular File Architecture

---

## Overview

This document describes the modular file structure of the RAG retrieval pipeline (Codeseek). Each file is a discrete, independently testable module with a single clearly scoped responsibility. The pipeline sits on top of the ingestion pipeline — it does not ingest, embed, or store. It takes a natural language query and returns a grounded LLM answer with source citations.

Qdrant must be running locally on port 6333 and already populated by the ingestion pipeline before any retrieval module can function.

---

## Top-Level Directory Layout

    retrieval/
        main.py
        config.py
        memory.py
        query_processor.py
        searcher.py
        expander.py
        assembler.py
        llm.py

Eight files. No subdirectories in V1. Each file maps to one stage of the pipeline. `config.py` and `memory.py` are shared infrastructure. The five stage files (`query_processor`, `searcher`, `expander`, `assembler`, `llm`) form the sequential processing chain. `main.py` is the entry point that wires all stages together.

---

## File-by-File Reference

---

### config.py

**Role:** Single source of truth for all tunable constants. No logic.

Every value that controls pipeline behavior lives here. No module hardcodes numeric limits, string names, or boolean flags inline. All modules import from config.

**Contents:**

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

    REPO_ROOT = "/absolute/path/to/ingested/repository"

**Dependencies:** None. This module imports nothing from the pipeline.

**Key design notes:**

`QUERY_PREFIX` is required for BGE asymmetric retrieval. The model was trained with a prefix distinction between passage vectors (stored at ingestion) and query vectors (used at retrieval). Omitting this prefix causes meaningfully lower cosine similarity scores.

`MAX_CONTEXT_TOKENS` is the total token budget for the assembled context block passed to the LLM. Conversation history is counted against this budget before code context is allocated. If the context token counter consistently hits 7000, reduce `CALL_EXPANSION_LIMIT` first, then consider reducing `TOP_K_AFTER_MERGE`.

`EXPAND_SIBLINGS` is off by default because fetching all other symbols from the same file tends to flood the context with loosely related code. Enable it only when the query intent is SYMBOL and the user is asking about a specific file.

---

### memory.py

**Role:** In-process conversation history storage. Store and retrieve the last N query/answer turns.

**Class: ConversationMemory**

    class ConversationMemory:
        def __init__(self, max_turns: int)
        def add(self, query: str, answer: str) -> None
        def get_history_block(self) -> str

`__init__` initializes an empty list `self.turns`. Each entry is a dict with keys `query` and `answer`.

`add` appends a new turn and pops the oldest entry if the list exceeds `max_turns`. This keeps memory bounded regardless of session length.

`get_history_block` returns a formatted string for prompt injection, or an empty string if no history exists.

    --- CONVERSATION HISTORY ---
    Q1: {query}
    A1: {answer}
    Q2: {query}
    A2: {answer}
    --- END HISTORY ---

**Dependencies:** None. No external imports beyond Python builtins.

**Key design notes:**

Memory is in-process only. It is not written to disk, not stored in a database, and not persisted between process restarts. This is intentional for a local personal tool — fresh sessions stay focused. File-based persistence is deferred to V2.

The history block is inserted into the prompt between the system prompt and the code context block. This ordering ensures the LLM resolves conversational references ("that function", "the one you mentioned") before reading the new code context. History tokens count against `MAX_CONTEXT_TOKENS` and are deducted from the code budget before any chunks are selected.

---

### query_processor.py

**Role:** Stage 1. Classify query intent and extract named entities from the raw query string using regex. No LLM call. No Qdrant call.

**Public interface:**

    def process_query(raw_query: str) -> dict

Returns:

    {
      "raw_query": str,
      "intent": "SYMBOL" | "DEPENDENCY" | "SEMANTIC",
      "entities": {
        "symbols": [str, ...],
        "files": [str, ...]
      }
    }

**Intent classification:**

Three intent types only. More would be overengineering for a single local repository.

SYMBOL — the user is asking about a named function, class, method, or file. Examples: "where is verify_token defined", "show me the Chunker class", "what does auth.py do", "list functions in storage.py". File-scoped queries fall under SYMBOL. A file hint in the query is treated as a symbol-level entity. The searcher handles it via a metadata filter on `relative_path`.

DEPENDENCY — the user wants to trace calls, usages, or relationships. Examples: "what calls process_chunk", "what depends on verify_token", "who uses the TokenService", "callers of embed_batch". Detected by the presence of dependency keywords in the query string.

SEMANTIC — everything else. General conceptual questions. Examples: "how does authentication work", "explain the ingestion flow", "why is there a sliding window in chunking". This is the default fallback. SEMANTIC always runs dense vector search, which is the most general-purpose retrieval path.

If no intent can be determined confidently, return SEMANTIC. Never leave intent unset.

**Entity extraction (regex, no LLM):**

Snake_case symbol names:

    \b[a-z][a-z0-9_]{2,}\b

CamelCase symbol names:

    \b[A-Z][a-zA-Z0-9]{2,}\b

File hints:

    \b\S+\.(py|js|ts|tsx|jsx)\b

Dependency keywords (signal DEPENDENCY intent when present):

    calls, imports, depends on, uses, references, callers of, called by, who uses

**Dependencies:** `re` (stdlib only). No Qdrant, no model, no config required at classification time.

---

### searcher.py

**Role:** Stage 2. Search Qdrant using one or more strategies based on query intent. Merge, deduplicate, and return a ranked candidate list.

**Public interface:**

    def search(query_info: dict) -> list[dict]

Takes the output of `process_query`. Returns a list of Qdrant payload dicts, each augmented with `retrieval_score` and `multi_layer_hit`.

**Layer A: Dense vector search (always runs)**

Embeds the query using the BGE model with the required `QUERY_PREFIX`. Calls `qdrant_client.search` with `limit=TOP_K_DENSE`. Returns the top matching chunks by cosine similarity.

The embedding model is loaded once at module import time, not per query. Loading SentenceTransformer per query is too slow for interactive use.

No hard score threshold is applied in V1. All `TOP_K_DENSE` results are returned and low-signal chunks are eliminated later by the token budget in the assembler.

**Layer B: Metadata filter search (runs when entities are detected)**

Runs in addition to Layer A when the query processor extracted symbol names or file hints.

For symbol lookups, prefer `qualified_symbol` (e.g. `src/auth.py::verify_token`) over `symbol_name` alone. `symbol_name` is ambiguous — `auth.login`, `admin.login`, and `user.login` are three different functions but share the same `symbol_name`. Use `qualified_symbol` when the user provides enough path context.

If no file path context exists in the query, fall back to `symbol_name` match with `limit=10`. This returns all symbols with that name across all files. The assembler deduplicates and the LLM sees all matches.

For file-scoped queries (when a `.py` or `.ts` filename is detected), filter by `relative_path` with `limit=30`. Return all chunks from the file and let the assembler rank and truncate to the token budget.

**Layer C: Calls graph search (runs for DEPENDENCY intent only)**

Searches chunks whose `calls[]` field contains the target symbol name, using Qdrant's `MatchAny` filter. Returns all chunks that call the target. Coverage depends on Tree-Sitter extraction quality at ingestion time.

**Merging and ranking:**

After all layers run, deduplicate by `chunk_id`. When the same chunk appears in multiple layers, keep it once and set `multi_layer_hit = True`.

Ranking order:

1. Chunks appearing in both dense and filter results (`multi_layer_hit = True`), sorted by cosine score descending.
2. Dense-only results, sorted by cosine score descending.
3. Filter/calls-only results, in scroll order.

Return up to `TOP_K_AFTER_MERGE` chunks.

**Dependencies:** `qdrant_client`, `sentence_transformers`, `config`.

---

### expander.py

**Role:** Stage 3. For each retrieved chunk, follow the metadata graph to pull in related chunks that were not directly retrieved but are needed for full context.

**Public interface:**

    def expand(candidates: list[dict], query_info: dict) -> list[dict]

Takes the ranked output of `search`. Returns a flat deduplicated list of all candidate chunks including expansions. Each chunk is augmented with `expansion_type`.

**Expansion 1: Reassemble split parts**

Controlled by `EXPAND_SPLIT_PARTS`. If a retrieved chunk has `total_parts > 1`, fetch all other parts of the same symbol. Identify by `relative_path + symbol_name`. Sort by `chunk_part` ascending so the assembler can concatenate them in order to reconstruct the full symbol body.

Mark these as `expansion_type = "split_part"`.

**Expansion 2: Parent class fetch**

Controlled by `EXPAND_PARENT`. If `chunk_type = "method"` and `parent_symbol` is set, fetch the class chunk that contains this method. This gives the LLM the class docstring, the list of other methods, and the class signature — sufficient to understand what the method belongs to without fetching all sibling methods.

Filter on `relative_path`, `symbol_name == parent_symbol`, and `chunk_type == "class"`. Use `limit=1`.

Mark these as `expansion_type = "parent_class"`.

**Expansion 3: Callee expansion**

Controlled by `EXPAND_CALLS`. For each retrieved chunk, collect all unique call targets from `calls[]`. Cap at `CALL_EXPANSION_LIMIT` unique targets total across all retrieved chunks (not per chunk). Look up each call target as a `symbol_name` in Qdrant with `limit=2`.

When the same call target name matches multiple symbols across different files, prefer the match in the same file as the calling chunk if one exists.

Depth is 1 hop only. Do not expand the `calls[]` of expanded chunks. Going deeper causes context explosion with rapidly diminishing relevance.

Mark these as `expansion_type = "callee"`.

**What is not expanded:**

Import expansion is not implemented. Following `imports[]` can explode context quickly — a single file importing jwt, database, settings, config, logger, and exceptions would flood the LLM context with noise.

Sibling expansion (other symbols from the same file) is off by default (`EXPAND_SIBLINGS = False`). Enable manually only when intent is SYMBOL and the query is clearly file-scoped.

**Output format per chunk:**

All original Qdrant payload fields, plus:

    expansion_type: "primary" | "split_part" | "parent_class" | "callee"
    retrieval_score: float   # cosine score if from dense search, else 0.0

**Dependencies:** `qdrant_client`, `config`.

---

### assembler.py

**Role:** Stage 4. Re-read source content from disk, rank chunks by tier, enforce the token budget, and produce the final context string to pass to the LLM.

**Public interface:**

    def assemble(
        expanded_chunks: list[dict],
        history_block: str
    ) -> tuple[str, list[dict], int]

Returns `(assembled_context_string, sources, context_token_count)`.

**Step 1: Read content from disk (cached)**

Content is not stored in Qdrant payloads. Re-read using `relative_path` and the `start_line` / `end_line` range stored in the payload.

    @lru_cache(maxsize=FILE_CACHE_MAX_SIZE)
    def read_file_lines(relative_path: str) -> tuple

The full file is cached as a tuple of lines. The same file is opened only once per process lifetime. For a local repository this is a significant speedup on multi-turn conversations that repeatedly reference the same files.

For split parts (`total_parts > 1`), read each part using its own line range and concatenate in chunk_part order.

Handle missing files gracefully. If a file no longer exists on disk (repository changed after ingestion), skip the chunk and log a warning. Do not raise an exception.

**Step 2: Rank chunks**

Sort the expanded candidate list by this priority order:

1. Primary chunks, sorted by `retrieval_score` descending.
2. Split parts of primary chunks.
3. Parent class chunks.
4. Callee chunks.

Within each tier, sort by `relative_path + start_line` ascending. This groups code from the same file together and presents it in source order, which is more readable for the LLM than a score-ordered shuffle across files.

**Step 3: Enforce token budget**

Reserve tokens for conversation history first:

    history_tokens = len(enc.encode(history_block))
    code_budget = MAX_CONTEXT_TOKENS - history_tokens

Walk the ranked chunk list. For each chunk, compute the formatted block token count using `tiktoken` with the `cl100k_base` encoding. Add the chunk if it fits within `code_budget`. Stop when budget is exhausted.

Budget truncation order when budget runs low: siblings first (if enabled), then callees, then parent classes, then split parts. Never truncate primary chunks.

Always include all primary chunks regardless of budget. If a single primary chunk exceeds the entire `code_budget`, include it anyway and truncate its content, appending `[content truncated to fit context budget]`.

**Step 4: Format context blocks**

Each selected chunk becomes a labeled block in the final context string.

Standard block (primary chunk):

    ### src/auth.py — verify_token (function, lines 10-25)
    Signature: def verify_token(token: str) -> dict
    Summary: Decodes and validates a JWT token, raising AuthError on failure.
    Calls: jwt.decode, raise_auth_error

    def verify_token(token: str) -> dict:
        ...

Expanded chunk (with inclusion label):

    ### src/auth.py — TokenService (class, lines 1-60)
    [included as: parent class of verify_token]
    Signature: class TokenService(BaseService)
    Summary: Service class managing all token operations including creation and validation.

    ### src/utils/jwt_helper.py — decode_jwt (function, lines 5-18)
    [included as: called by verify_token]
    Signature: def decode_jwt(token: str, secret: str) -> dict
    Summary: Low-level JWT decode wrapper around the PyJWT library.

Blocks are separated by a blank line. The entire set forms the `assembled_context_string`.

**Output:**

`assembled_context_string` — single string passed to the LLM stage.

`sources` — list of dicts, one per included chunk, each containing `relative_path`, `symbol_name`, `start_line`, `end_line`, `expansion_type`. Used by `main.py` to print the sources block.

`context_token_count` — integer. Total tokens used. Printed at the end of each turn for debugging.

**Dependencies:** `qdrant_client` (for split part queries), `tiktoken`, `functools.lru_cache`, `pathlib`, `config`.

---

### llm.py

**Role:** Stage 5. Build the full prompt, call Groq, and return the answer string.

**Public interface:**

    def generate_answer(
        raw_query: str,
        assembled_context: str,
        history_block: str
    ) -> str

**System prompt (static, hardcoded):**

    You are a code assistant with full context of a software repository.
    You will be given relevant code chunks extracted from the repository.
    Answer the user's question using only the provided code context.
    When referencing code, always cite: file path, symbol name, and line numbers.
    If the answer requires code that is not in the provided context, say so explicitly.
    Do not invent function signatures, variable names, or behavior not shown in the context.
    Be concise. Prefer showing the relevant code path over lengthy prose explanations.

**Full prompt assembly order:**

    [system prompt]
    {history_block}           <- empty string if no history exists
    --- CODE CONTEXT ---
    {assembled_context_string}
    --- END CODE CONTEXT ---
    Question: {raw_query}

The history block is placed before the code context. This lets the LLM resolve conversational references from prior turns before reading the new code context.

**Primary LLM: Groq (Llama 3.1 70B)**

Free tier. Fast inference. API key from https://console.groq.com.

    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model="llama-3.1-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=MAX_RESPONSE_TOKENS
    )
    answer = response.choices[0].message.content

**Dependencies:** `groq`, `config`.

---

### main.py

**Role:** Entry point. Interactive REPL loop. Wires all pipeline stages in sequence. Maintains `ConversationMemory` across turns.

**Imports:**

    from retrieval.query_processor import process_query
    from retrieval.searcher import search
    from retrieval.expander import expand
    from retrieval.assembler import assemble
    from retrieval.llm import generate_answer
    from retrieval.memory import ConversationMemory
    from retrieval.config import CONVERSATION_HISTORY_TURNS, MAX_CONTEXT_TOKENS, REPO_ROOT

**Per-turn pipeline execution:**

    history_block  = memory.get_history_block()
    query_info     = process_query(raw_query)
    candidates     = search(query_info)
    expanded       = expand(candidates, query_info)
    context, sources, token_count = assemble(expanded, history_block)
    answer         = generate_answer(raw_query, context, history_block)
    memory.add(raw_query, answer)

**Output per turn:**

Prints the LLM answer, then the sources block, then the context token count:

    Sources used:
      - src/auth.py :: verify_token (lines 10-25) [primary]
      - src/auth.py :: TokenService (lines 1-60) [parent class]
      - src/utils/jwt_helper.py :: decode_jwt (lines 5-18) [callee]

    [context tokens: 3241 / 7000]

**Run modes:**

Interactive (default):

    uv run python -m retrieval.main

Single query (non-interactive):

    uv run python -m retrieval.main --query "how does token verification work"

Exits cleanly on Ctrl+C, EOF, or the `exit` / `quit` command.

---

## Module Dependency Graph

    config.py
        ^
        |  (imported by all modules below)
        |
    memory.py         (no deps beyond stdlib)
    query_processor.py (no deps beyond stdlib + config)
    searcher.py        (config, qdrant_client, sentence_transformers)
    expander.py        (config, qdrant_client)
    assembler.py       (config, qdrant_client, tiktoken, pathlib, functools)
    llm.py             (config, groq)

    main.py            (imports all of the above)

No module imports from another pipeline module except `main.py`. The only shared dependency is `config.py`. This keeps each stage independently importable and testable without instantiating the full pipeline.

---

## Implementation Status

Retrieval is implemented with the documented module boundaries and stage flow:

- `query_processor.py` for intent/entity extraction.
- `searcher.py` for dense + metadata + dependency retrieval.
- `expander.py` for split/parent/callee expansion.
- `assembler.py` for disk-backed context assembly and budgeting.
- `llm.py` for Groq completion calls.
- `memory.py` for in-process turn history.
- `main.py` for REPL and single-query execution.

---

## Qdrant Payload Schema (Required Before Retrieval)

The retrieval pipeline depends on ingestion-populated metadata fields: `qualified_symbol`,
`signature`, `docstring`, and `summary`. If these fields are absent in Qdrant payloads,
re-run ingestion with the current schema before running retrieval.

Full payload per chunk:

    {
      "chunk_id":        "a3f9...",
      "file_path":       "/abs/path/auth.py",
      "relative_path":   "src/auth.py",
      "language":        "python",
      "chunk_type":      "function",
      "symbol_name":     "verify_token",
      "qualified_symbol":"src/auth.py::verify_token",
      "parent_symbol":   null,
      "signature":       "def verify_token(token: str) -> dict",
      "start_line":      10,
      "end_line":        25,
      "chunk_part":      1,
      "total_parts":     1,
      "token_count":     128,
      "imports":         ["jwt", "datetime"],
      "calls":           ["jwt.decode"],
      "docstring":       "Validate JWT token.",
      "summary":         "Decodes and validates a JWT token, returning the payload or raising AuthError."
    }

Content is not stored in the payload. It is re-read from disk at retrieval time by the assembler.

---

## Known Gaps (V1)

`calls[]` coverage is incomplete. Tree-Sitter extracts explicit function calls but misses dynamic dispatch, callbacks, and some method chains. Callee expansion is best-effort.

Content re-read from disk can diverge from Qdrant metadata if the repository changes after ingestion. Run incremental ingestion after significant code changes.

Conversation memory is in-process only. Cleared on restart. File-based persistence is deferred to V2.

LLM-generated summaries at ingestion time cost API calls and ingestion time. For large repositories, run ingestion in batches or overnight to stay within free tier rate limits.

No BM25 / sparse search. Dense retrieval handles the vast majority of queries. Add only if short or uncommon symbol names consistently fail to retrieve correctly.

---

## System Requirements

    Python 3.10+
    Qdrant running locally on port 6333
    uv pip install qdrant-client sentence-transformers tiktoken groq
