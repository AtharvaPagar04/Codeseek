# RAG Retrieval Pipeline — Local Development Documentation

---

## What This Is

A local retrieval and query processing pipeline that sits on top of the ingestion pipeline.
It takes a natural language query, searches the Qdrant vector store, expands references
using the metadata already stored at ingestion time, assembles context from disk, and
sends everything to an LLM to produce a grounded answer.

This is retrieval only. Ingestion is handled separately by the ingestion pipeline.
This pipeline assumes Qdrant is running locally and already populated by a previous ingestion run.

---

## What Is Not In This Pipeline

These were considered and deliberately excluded for V1.

Cross-encoder re-ranking: adds an extra model, extra latency, extra complexity. Dense search
is already good enough for a single local repository with 10k-50k chunks.

Hybrid BM25 / sparse search: BGE dense search plus metadata filter lookup already covers
the vast majority of code navigation queries. Add only if retrieval quality becomes a
real problem in practice.

Import expansion: following imports[] can explode context quickly. A single file importing
jwt, database, settings, config, logger, and exceptions would flood the LLM context with
noise. Parent + callee expansion already provides sufficient cross-reference coverage.

Repository metadata retrieval: language breakdown, size stats, monorepo detection. Useful
for dashboards, not useful for answering code questions.

---

## System Requirements

Python 3.10+
Qdrant running locally on port 6333 (same instance used by ingestion)
sentence-transformers (same model used at ingestion: BAAI/bge-small-en-v1.5)
qdrant-client
tiktoken
google-generativeai

Install:

    uv pip install qdrant-client sentence-transformers tiktoken google-generativeai

---

## High Level Flow

    User Query (natural language string)
        |
        v
    Conversation Memory     -- prepend last N turns for context continuity
        |
        v
    Query Processor         -- classify intent (SYMBOL / DEPENDENCY / SEMANTIC)
                               extract symbol names and file hints
        |
        v
    Searcher                -- dense vector search always
                               metadata filter search when entities detected
                               calls graph search when DEPENDENCY intent
        |
        v
    Reference Expander      -- reassemble split parts
                               fetch parent class for methods
                               1-hop callee expansion via calls[]
        |
        v
    Context Assembler       -- re-read content from disk (lru_cache)
                               rank by tier, enforce token budget
                               format labeled context blocks
        |
        v
    LLM (Gemini Flash)      -- system prompt + conversation history + context + query
        |
        v
    Answer + source citations
        |
        v
    Conversation Memory     -- store turn for next query

---

## Ingestion Contract for Retrieval

The retrieval pipeline depends on metadata populated by the ingestion pipeline and stored
in Qdrant payloads. These fields are expected to exist before retrieval runs.

### 1. qualified_symbol

Problem: symbol_name alone is ambiguous. auth.login, admin.login, and user.login are three
different functions but all have symbol_name = "login". This causes incorrect filter matches.

Fix: store a qualified identifier that combines file path and symbol name.

    "qualified_symbol": "src/auth.py::login"

Format: {relative_path}::{symbol_name}
For methods: {relative_path}::{parent_symbol}.{symbol_name}

Example:

    "qualified_symbol": "src/auth.py::TokenService.verify_token"

Use this field in metadata filter searches instead of symbol_name alone wherever ambiguity
is possible.

### 2. signature

Store the function or method signature as a string. Extract at parse time from the AST.

    "signature": "def login(username, password)"
    "signature": "async def fetch_user(user_id: int) -> User"
    "signature": "function processChunk(chunk, options = {})"

For classes, store the class declaration line:

    "signature": "class TokenService(BaseService)"

This is small in storage size but high in value. It lets the assembler show the LLM
the exact interface of a symbol without needing the full function body. It also makes
the formatted context blocks more readable at a glance.

### 3. docstring

The ingestion pipeline already extracts docstrings during AST parsing and includes them
in the embedding input. Store the docstring explicitly as a separate payload field too.

    "docstring": "Validates a JWT token and returns the decoded user payload."

If no docstring exists, store null or empty string.

Docstrings often match user queries better than raw code does. When a user asks
"how does token verification work", the docstring "Validates a JWT token..." is a
stronger semantic match than the function body full of jwt.decode() calls.

### 4. summary

The retrieval context block format already has a Summary: line:

    Summary: {summary}

Current ingestion uses deterministic AST-based summaries like:

    Function: verify_token
    Parameters: token
    Docstring: Validate JWT token.

This is sufficient for V1 retrieval. As an optional quality upgrade, ingestion can later
switch summary generation to an LLM-produced one-sentence behavioral summary.

### Updated Qdrant Payload Schema

The full payload stored per chunk after these additions:

    {
      "chunk_id": "a3f9...",
      "file_path": "/abs/path/auth.py",
      "relative_path": "src/auth.py",
      "language": "python",
      "chunk_type": "function",
      "symbol_name": "verify_token",
      "qualified_symbol": "src/auth.py::verify_token",
      "parent_symbol": null,
      "signature": "def verify_token(token: str) -> dict",
      "start_line": 10,
      "end_line": 25,
      "chunk_part": 1,
      "total_parts": 1,
      "token_count": 128,
      "imports": ["jwt", "datetime"],
      "calls": ["jwt.decode"],
      "docstring": "Validate JWT token.",
      "summary": "Decodes and validates a JWT token, returning the payload or raising AuthError."
    }

Note: content is still not stored in the payload. Re-read from disk at retrieval time.

---

## Pipeline Config

All tuneable values live in config.py. These are the defaults to start with.

    COLLECTION_NAME = "repository_chunks"
    QDRANT_HOST = "localhost"
    QDRANT_PORT = 6333

    EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM = 384
    QUERY_PREFIX = "query: "            # BGE asymmetric retrieval requires this prefix

    TOP_K_DENSE = 15                    # candidates from dense vector search
    TOP_K_AFTER_MERGE = 10              # after merging dense + filter results, before expansion
    MAX_CONTEXT_TOKENS = 7000           # token budget for LLM context block
    MAX_RESPONSE_TOKENS = 1024          # expected LLM response size

    EXPAND_CALLS = True                 # follow calls[] to find referenced symbols
    EXPAND_PARENT = True                # fetch parent class when a method is retrieved
    EXPAND_SIBLINGS = False             # fetch other symbols from same file (noisy, off by default)
    EXPAND_SPLIT_PARTS = True           # reassemble chunks that were split at ingestion
    CALL_EXPANSION_LIMIT = 5            # max unique call targets to look up per query

    CONVERSATION_HISTORY_TURNS = 5      # how many prior turns to include in prompt
    FILE_CACHE_MAX_SIZE = 128           # lru_cache size for disk reads (number of files)

    REPO_ROOT = "/absolute/path/to/ingested/repository"    # must match the repository that was ingested

---

## File Structure

    retrieval/
        main.py                 entry point: interactive loop, conversation memory
        query_processor.py      Stage 1: intent classification, entity extraction
        searcher.py             Stage 2: dense + metadata filter + calls search
        expander.py             Stage 3: reference expansion (calls, parent, parts)
        assembler.py            Stage 4: disk reads (cached), ranking, token budget
        llm.py                  Stage 5: prompt builder + Gemini client
        memory.py               conversation turn storage and retrieval
        config.py               all configuration constants

---

## Conversation Memory

### Responsibility

Store the last N query/answer pairs and prepend them to each LLM prompt.
This is the single highest-value feature for day-to-day usability.

Without it:

    Query 1: "explain the authentication flow"
    Query 2: "where is the JWT verified?"        <-- LLM has no idea what JWT you mean
    Query 3: "what calls that?"                  <-- LLM has no idea what "that" refers to

With it, the LLM has the thread of conversation and can resolve references like "that",
"it", "the function you mentioned", and "what about the other one".

### Implementation

memory.py holds a simple in-process list. No database, no file persistence, no Redis.
Memory lives for the duration of the process. When you restart, history is cleared.
This is intentional for a local tool. Fresh sessions stay focused.

    class ConversationMemory:
        def __init__(self, max_turns: int):
            self.max_turns = max_turns
            self.turns = []   # list of {"query": str, "answer": str}

        def add(self, query: str, answer: str) -> None:
            self.turns.append({"query": query, "answer": answer})
            if len(self.turns) > self.max_turns:
                self.turns.pop(0)

        def get_history_block(self) -> str:
            if not self.turns:
                return ""
            lines = ["--- CONVERSATION HISTORY ---"]
            for i, turn in enumerate(self.turns):
                lines.append(f"Q{i+1}: {turn['query']}")
                lines.append(f"A{i+1}: {turn['answer']}")
            lines.append("--- END HISTORY ---")
            return "\n".join(lines)

### How It Enters the Prompt

The conversation history block is inserted between the system prompt and the code context:

    [system prompt]
    [conversation history block]    <-- injected here if history exists
    [code context block]
    Question: {current query}

This means the LLM sees: who it is, what was discussed before, what code is relevant now,
and the current question. In that order.

History tokens count against MAX_CONTEXT_TOKENS. If history is large, reduce code context
to stay within budget. History takes priority over expanded context chunks.

---

## Stage 1: Query Processor

### Responsibility

Classify the user query into one of three intent types and extract named entities.
Three intents are enough. More is overengineering for this use case.

### Intent Types

    SYMBOL          user is asking about a named function, class, method, or file
                    examples: "where is verify_token defined"
                              "show me the Chunker class"
                              "what does auth.py do"
                              "list functions in storage.py"

                    Note: file-scoped queries fall under SYMBOL, not a separate intent.
                    A file hint in the query is treated as a symbol-level entity lookup.
                    The searcher handles it via metadata filter on relative_path.

    DEPENDENCY      user wants to trace calls, usages, or relationships
                    examples: "what calls process_chunk"
                              "what depends on verify_token"
                              "who uses the TokenService"
                              "callers of embed_batch"

    SEMANTIC        everything else, general conceptual questions
                    examples: "how does authentication work"
                              "explain the ingestion flow"
                              "how does the parser connect to the chunker"
                              "why is there a sliding window in chunking"

                    Cross-file questions fall here. They are semantic retrieval plus
                    expansion, no special logic needed.

### Entity Extraction

Extract from the raw query string using regex. No LLM needed here.

Symbol names:
    snake_case pattern:  \b[a-z][a-z0-9_]{2,}\b
    CamelCase pattern:   \b[A-Z][a-zA-Z0-9]{2,}\b
    Examples: verify_token, ChunkGenerator, process_file

File hints:
    Pattern: \b\S+\.(py|js|ts|tsx|jsx)\b
    Examples: auth.py, embedder.ts

Dependency keywords (signal DEPENDENCY intent):
    calls, imports, depends on, uses, references, callers of, called by, who uses

### Output

    {
      "raw_query": "what calls verify_token",
      "intent": "DEPENDENCY",
      "entities": {
        "symbols": ["verify_token"],
        "files": []
      }
    }

### Fallback

If no intent can be confidently determined, default to SEMANTIC.
SEMANTIC always runs dense vector search which is the most general-purpose path.

---

## Stage 2: Searcher

### Responsibility

Search Qdrant using one or more strategies depending on query intent.
Merge results, deduplicate by chunk_id, and return a ranked candidate list.

### Layer A: Dense Vector Search (always runs)

Embed the user query with the same model used at ingestion time.
Prepend the BGE query prefix. This is required for BGE asymmetric retrieval.
Without the prefix, cosine similarity scores will be significantly lower.

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    query_vector = model.encode(QUERY_PREFIX + raw_query).tolist()

    results = qdrant_client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=TOP_K_DENSE,
        with_payload=True
    )

Load the model once at startup, not per query.

Do not apply a hard score threshold in V1. Return all TOP_K_DENSE results and let
the assembler truncate low-signal chunks via the token budget.

### Layer B: Metadata Filter Search (runs when entities are present)

If the query processor extracted symbol names or file hints, run a scroll search
with payload filters in addition to dense search.

Symbol lookup using qualified_symbol for precision:

    from qdrant_client.models import Filter, FieldCondition, MatchValue

    results = qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(
                key="qualified_symbol",
                match=MatchValue(value="src/auth.py::verify_token")
            )]
        ),
        limit=5,
        with_payload=True
    )

If the user did not provide a file prefix (just "verify_token" with no path), fall back
to symbol_name match. This returns all symbols with that name across all files. The
assembler deduplicates and the LLM sees all matches.

    results = qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="symbol_name", match=MatchValue(value="verify_token"))]
        ),
        limit=10,
        with_payload=True
    )

File-scoped lookup (when a .py/.ts file name is detected in the query):

    results = qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="relative_path", match=MatchValue(value="src/auth.py"))]
        ),
        limit=30,
        with_payload=True
    )

Return all chunks from the file. The assembler ranks and truncates to token budget.

### Layer C: Calls Graph Search (runs for DEPENDENCY intent)

When the user asks what calls a symbol, search chunks whose calls[] field contains it.

    from qdrant_client.models import MatchAny

    results = qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="calls", match=MatchAny(any=["verify_token"]))]
        ),
        limit=10,
        with_payload=True
    )

This returns all chunks that call the target symbol. Coverage depends on Tree-Sitter
extraction quality at ingestion time. Best for Python, partial for JS/TS.

### Merging Results

After all layers run, merge into a single list. Deduplicate by chunk_id.
When the same chunk appears in multiple layers, keep it once and flag it as multi_layer_hit = True.

Ranking order after merge:
1. Chunks appearing in both dense and filter results (multi_layer_hit = True), sorted by score
2. Dense-only results, sorted by cosine score descending
3. Filter/calls-only results, in scroll order

Return up to TOP_K_AFTER_MERGE chunks for the expansion stage.

---

## Stage 3: Reference Expander

### Responsibility

For each retrieved chunk, follow the metadata graph to pull in related chunks
that were not directly retrieved but are needed for full context.

### Expansion 1: Reassemble Split Parts

If a retrieved chunk has total_parts > 1, fetch all other parts of the same symbol.
Identify by relative_path + symbol_name. Sort by chunk_part ascending.
The assembler concatenates them in order to reconstruct the full symbol body.

    results = qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[
            FieldCondition(key="relative_path", match=MatchValue(value=chunk["relative_path"])),
            FieldCondition(key="symbol_name", match=MatchValue(value=chunk["symbol_name"]))
        ]),
        limit=20,
        with_payload=True
    )

Mark these as expansion_type = "split_part".

### Expansion 2: Parent Class Fetch

If chunk_type = "method" and parent_symbol is set, fetch the class chunk that
contains this method. This gives the LLM the class docstring, the list of other
methods, and the class signature — enough to understand what the method belongs to.

    results = qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[
            FieldCondition(key="relative_path", match=MatchValue(value=chunk["relative_path"])),
            FieldCondition(key="symbol_name", match=MatchValue(value=chunk["parent_symbol"])),
            FieldCondition(key="chunk_type", match=MatchValue(value="class"))
        ]),
        limit=1,
        with_payload=True
    )

Mark these as expansion_type = "parent_class".

### Expansion 3: Callee Expansion

For each retrieved chunk, collect all unique symbols in calls[].
Cap at CALL_EXPANSION_LIMIT unique targets total across all retrieved chunks.
Look up each call target as a symbol in Qdrant.

    results = qdrant_client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="symbol_name", match=MatchValue(value=call_target))]
        ),
        limit=2,
        with_payload=True
    )

Use limit=2. A call target name can match multiple symbols across different files.
Prefer the match in the same file as the calling chunk if one exists.

Depth: 1 hop only. Do not expand the calls[] of expanded chunks.
Going deeper causes context explosion with rapidly diminishing relevance.

Mark these as expansion_type = "callee".

### What Is Not Expanded

Import expansion is not implemented. Following imports[] adds too much noise.
Sibling expansion (other symbols from the same file) is off by default (EXPAND_SIBLINGS = False).
Enable it manually only when intent = SYMBOL and the query is clearly file-scoped.

### Output

A flat deduplicated list of candidate chunks. Each has:
    - all original Qdrant payload fields
    - expansion_type: "primary" | "split_part" | "parent_class" | "callee"
    - retrieval_score: cosine score if from dense search, else 0.0

---

## Stage 4: Context Assembler

### Responsibility

Re-read source content from disk, rank chunks, enforce the token budget, and
produce the final context string to pass to the LLM.

### Step 1: Read Content from Disk (Cached)

Content is not stored in Qdrant. Re-read from disk using relative_path + line range.
Cache file reads in memory using functools.lru_cache to avoid re-opening the same
file on every query.

    from functools import lru_cache
    from pathlib import Path

    @lru_cache(maxsize=FILE_CACHE_MAX_SIZE)
    def read_file_lines(relative_path: str) -> tuple:
        file_path = Path(REPO_ROOT) / relative_path
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return tuple(f.readlines())

    def read_chunk_content(chunk: dict) -> str:
        lines = read_file_lines(chunk["relative_path"])
        start = max(0, chunk["start_line"] - 1)
        end = chunk["end_line"]
        return "".join(lines[start:end])

lru_cache stores the full file as a tuple of lines. The same file is only opened once
per process lifetime. For a local repo this is a significant speedup on multi-turn
conversations that keep returning to the same files.

Handle missing files gracefully. If the file no longer exists, skip the chunk and log.
This can happen when the repo changes between ingestion and query time.

For split parts (total_parts > 1), read each part using its own line range.
The assembler concatenates parts in order.

### Step 2: Rank Chunks

Sort the expanded candidate list using this priority order:

    1. Primary chunks, sorted by retrieval_score descending
    2. Split parts of primary chunks
    3. Parent class chunks
    4. Callee chunks

Within each tier, sort by relative_path + start_line ascending.
This groups code from the same file together and presents it in line order,
which is more readable for the LLM.

### Step 3: Token Budget

Reserve tokens for conversation history first. Whatever is left goes to code context.

    history_tokens = len(enc.encode(history_block))
    code_budget = MAX_CONTEXT_TOKENS - history_tokens

Walk the ranked chunk list. For each chunk, compute the formatted block token count.
Add it if it fits within code_budget. Stop when budget is exhausted.

    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")

    used_tokens = 0
    selected_chunks = []

    for chunk in ranked_chunks:
        block = format_chunk_block(chunk, content)
        block_tokens = len(enc.encode(block))
        if used_tokens + block_tokens <= code_budget:
            selected_chunks.append((chunk, content))
            used_tokens += block_tokens
        else:
            break

Always include all primary chunks regardless of budget.
If a single primary chunk exceeds the entire code_budget, include it anyway and truncate
its content, appending "[content truncated to fit context budget]".

Truncation order when budget runs out: siblings first (if enabled), then callees,
then parent classes, then split parts. Never truncate primary chunks.

### Step 4: Format Context Blocks

Each selected chunk becomes a labeled block in the final context string.

Standard block format:

    ### src/auth.py — verify_token (function, lines 10-25)
    Signature: def verify_token(token: str) -> dict
    Summary: Decodes and validates a JWT token, raising AuthError on failure.
    Calls: jwt.decode, raise_auth_error

    def verify_token(token: str) -> dict:
        ...

For expanded chunks, add a label explaining why they are included:

    ### src/auth.py — TokenService (class, lines 1-60)
    [included as: parent class of verify_token]
    Signature: class TokenService(BaseService)
    Summary: Service class managing all token operations including creation and validation.

    ### src/utils/jwt_helper.py — decode_jwt (function, lines 5-18)
    [included as: called by verify_token]
    Signature: def decode_jwt(token: str, secret: str) -> dict
    Summary: Low-level JWT decode wrapper around the PyJWT library.

Separate each block with a blank line. The entire set of blocks forms the
assembled_context_string passed to the LLM stage.

### Output

assembled_context_string: single string, all context blocks concatenated.
sources: list of {relative_path, symbol_name, start_line, end_line, expansion_type}.
context_token_count: total tokens used.

---

## Stage 5: LLM

### Responsibility

Build the full prompt including conversation history and code context, call Gemini Flash,
and return the answer with source citations.

### LLM Choice

Primary: Google Gemini 2.0 Flash via Google AI Studio.
Free tier. 1 million token context window. Generous rate limits for personal use.
API key: https://aistudio.google.com

Fallback: Groq (Llama 3.1 70B or DeepSeek R1 Distill).
Free tier. 128K context. Very fast inference.
API key: https://console.groq.com

### System Prompt

    You are a code assistant with full context of a software repository.
    You will be given relevant code chunks extracted from the repository.
    Answer the user's question using only the provided code context.
    When referencing code, always cite: file path, symbol name, and line numbers.
    If the answer requires code that is not in the provided context, say so explicitly.
    Do not invent function signatures, variable names, or behavior not shown in the context.
    Be concise. Prefer showing the relevant code path over lengthy prose explanations.

### Full Prompt Assembly

    [system prompt]

    {history_block}                  <- empty string if no history yet

    --- CODE CONTEXT ---

    {assembled_context_string}

    --- END CODE CONTEXT ---

    Question: {raw_query}

The history block appears before the code context. This ordering lets the LLM resolve
references from prior turns ("that function", "the one you mentioned") before reading
the new code context.

### Gemini API Call

    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        "gemini-2.0-flash",
        system_instruction=system_prompt
    )

    response = model.generate_content(
        full_prompt,
        generation_config=genai.GenerationConfig(max_output_tokens=MAX_RESPONSE_TOKENS)
    )
    answer = response.text

### Groq Fallback

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

### Output

Print the LLM answer, then the sources block:

    Sources used:
      - src/auth.py :: verify_token (lines 10-25) [primary]
      - src/auth.py :: TokenService (lines 1-60) [parent class]
      - src/utils/jwt_helper.py :: decode_jwt (lines 5-18) [callee]

    [context tokens: 3241 / 7000]

The token count at the end is useful for debugging. If you see it hitting 7000 consistently,
reduce CALL_EXPANSION_LIMIT or MAX_CONTEXT_TOKENS to leave more room for the answer.

---

## main.py: Wiring It Together

Interactive loop. Reads query from stdin. Maintains conversation memory across turns.
Exits cleanly on Ctrl+C or the "exit" command.

    import sys
    from retrieval.query_processor import process_query
    from retrieval.searcher import search
    from retrieval.expander import expand
    from retrieval.assembler import assemble
    from retrieval.llm import generate_answer
    from retrieval.memory import ConversationMemory
    from retrieval.config import CONVERSATION_HISTORY_TURNS

    def main():
        memory = ConversationMemory(max_turns=CONVERSATION_HISTORY_TURNS)

        print("Codeseek ready. Type your question or 'exit' to quit.")
        print(f"Repository: {REPO_ROOT}")
        print()

        while True:
            try:
                raw_query = input(">>> ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                break

            if not raw_query:
                continue
            if raw_query.lower() in ("exit", "quit"):
                break

            history_block = memory.get_history_block()
            query_info = process_query(raw_query)
            candidates = search(query_info)
            expanded = expand(candidates, query_info)
            context, sources, token_count = assemble(expanded, history_block)
            answer = generate_answer(raw_query, context, history_block)

            print()
            print(answer)
            print()
            print("Sources:")
            for src in sources:
                label = f" [{src['expansion_type']}]" if src["expansion_type"] != "primary" else ""
                print(f"  {src['relative_path']} :: {src['symbol_name']} (lines {src['start_line']}-{src['end_line']}){label}")
            print(f"[context tokens: {token_count} / {MAX_CONTEXT_TOKENS}]")
            print()

            memory.add(raw_query, answer)

    if __name__ == "__main__":
        main()

Run it:

    uv run python -m retrieval.main

Single query mode (non-interactive):

    uv run python -m retrieval.main --query "how does token verification work"

---

## Build Order

Build in this order. Each stage is independently testable before wiring the next.

1. searcher.py
   Dense search only. Print symbol_name, relative_path, score for a test query.
   Verify BGE query prefix is applied and scores look reasonable (0.6+ for relevant results).

2. assembler.py (disk read + format only, no budget logic yet)
   Take searcher output, read content from disk, print formatted context blocks.
   Verify line ranges are correct and lru_cache works.

3. llm.py
   Hard-code a small context string. Call Gemini. Verify API key and response parsing work.

4. main.py (wire steps 1-3, no memory yet)
   End-to-end: query -> search -> assemble -> LLM -> answer.
   Already useful at this point. Pause here and test with real queries.

5. memory.py + wire into main.py
   Add ConversationMemory. Test a multi-turn sequence to verify history is passed correctly.

6. query_processor.py
   Add intent classification and entity extraction.
   Wire into searcher to activate Layer B and Layer C.

7. expander.py
   Add split-part reassembly first (simplest). Then parent class fetch. Then callee expansion.
   Each is a config flag. Test each one independently before enabling the next.

---

## Known Gaps (V1)

1. calls[] coverage is incomplete. Tree-Sitter extracts explicit function calls but misses
   dynamic dispatch, callbacks, and some method chains. Callee expansion is best-effort.

2. Content re-read from disk can diverge from Qdrant metadata if the repository changes
   after ingestion. Run incremental ingestion after significant code changes.

3. Conversation memory is in-process only. Cleared on restart. For a local personal tool
   this is fine. File-based persistence can be added in V2 if needed.

4. LLM-generated summaries at ingestion time cost API calls and ingestion time. For large
   repositories, run ingestion in batches or overnight to stay within free tier rate limits.

5. No BM25 / sparse search. Dense retrieval handles the vast majority of queries. Add only
   if you find short or uncommon symbol names consistently failing to retrieve correctly.

---

## Smoke Test

After building stages 1-4, run with a real query:

    uv run python -m retrieval.main --query "how does embedding work"

Expected: answer references the embedder file, cites specific functions and line numbers.
Sources block should show at least one primary hit and ideally one callee.

Manual Qdrant check before building retrieval:

    from qdrant_client import QdrantClient
    client = QdrantClient("localhost", port=6333)
    results = client.search(
        collection_name="repository_chunks",
        query_vector=[0.0] * 384,
        limit=3
    )
    for r in results:
        print(r.payload["symbol_name"])
        print(r.payload["qualified_symbol"])
        print(r.payload["signature"])
        print(r.payload["summary"])
        print(r.payload["calls"])
        print()

If `qualified_symbol`, `signature`, or other required fields are missing in payloads,
re-run ingestion so Qdrant is upserted with the current schema before running retrieval.
