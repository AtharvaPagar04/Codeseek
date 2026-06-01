# RAG Ingestion Pipeline — Modular Architecture

---

## Changelog from previous version

Four mandatory changes applied:

1. Chunk dataclass gains `parameters` and `methods` fields
2. parser.py output replaced with ParsedFile and ParsedSymbol dataclasses
3. models/parsed.py added to hold those dataclasses
4. chunk_id hash now includes parent_symbol to avoid collisions between
   same-named methods in different classes

One recommended change applied:

5. File-level chunk summary now receives a symbols list via chunker.py
   instead of trying to reconstruct it later

---

## Folder Structure

    rag_ingestion/
    │
    ├── main.py
    ├── config.py
    │
    ├── stages/
    │   ├── __init__.py
    │   ├── loader.py
    │   ├── discovery.py
    │   ├── filtering.py
    │   ├── language.py
    │   ├── parser.py
    │   ├── chunker.py
    │   ├── overflow.py
    │   ├── metadata.py
    │   ├── summary.py
    │   ├── embedder.py
    │   └── storage.py
    │
    ├── models/
    │   ├── __init__.py
    │   ├── file.py
    │   ├── chunk.py
    │   └── parsed.py          # NEW — ParsedFile and ParsedSymbol live here
    │
    └── utils/
        ├── __init__.py
        ├── logger.py
        └── counters.py

---

## File Responsibilities

---

### main.py

Entry point. Wires all stages together in order. Reads CLI args. Prints final report.

Responsibilities:
- Parse CLI argument (repo path or GitHub URL)
- Call each stage in sequence
- Pass output of each stage as input to next
- Print final counters at end

Does NOT contain any logic. Only orchestration.

    python main.py /path/to/repo
    python main.py https://github.com/user/project

---

### config.py

All tunable constants in one place. Nothing is hardcoded elsewhere.

    QDRANT_HOST         = "localhost"
    QDRANT_PORT         = 6333
    COLLECTION_NAME     = "repository_chunks"
    EMBEDDING_MODEL     = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM       = 384
    MAX_CHUNK_TOKENS    = 2048
    BATCH_SIZE          = 128
    SLIDING_WINDOW_SIZE = 100   # lines
    SLIDING_OVERLAP     = 20    # lines
    TEMP_CLONE_DIR      = "/tmp/rag_ingestion"

---

## models/

Plain dataclasses. No logic. Just typed containers passed between stages.

---

### models/file.py

No changes from previous version.

    @dataclass
    class FileRecord:
        path: str               # absolute path
        relative_path: str      # relative to repo root
        extension: str
        size_bytes: int
        language: str = ""      # filled in by language.py
        skipped: bool = False
        skip_reason: str = ""

---

### models/parsed.py

NEW FILE. Replaces the raw dict that parser.py previously returned.
Imported by parser.py (to build) and chunker.py (to consume).

    @dataclass
    class ParsedSymbol:
        symbol_name: str
        symbol_type: str            # "function" | "class" | "method"
        parent_symbol: str          # empty string if top-level
        start_line: int
        end_line: int
        parameters: list[str]       # parameter names, empty list if none
        methods: list[str]          # method names for class symbols, empty list otherwise
        docstring: str              # empty string if none
        calls: list[str]            # function/method names called inside this symbol


    @dataclass
    class ParsedFile:
        relative_path: str
        language: str
        parse_status: str           # "ok" | "failed"
        imports: list[str]          # all imports at file level
        symbols: list[ParsedSymbol] # empty list if parse_status is "failed"

Why parameters lives on ParsedSymbol and not Chunk:
    parser.py extracts parameters from the AST and stores them on ParsedSymbol.
    chunker.py copies them onto the Chunk.
    summary.py reads them from the Chunk to build the summary string.
    Without this field on Chunk, summary.py has no access to parameter data.

Why methods lives on ParsedSymbol and not Chunk:
    Same reason. For a class symbol, parser.py collects its method names into methods.
    chunker.py copies them onto the Chunk.
    summary.py needs them to build "Class UserService\nMethods: create_user, update_user".

---

### models/chunk.py

Two fields added: parameters and methods.

    @dataclass
    class Chunk:
        chunk_id: str
        file_path: str
        relative_path: str
        language: str
        chunk_type: str             # "function" | "class" | "method" | "file"
        symbol_name: str
        parent_symbol: str
        start_line: int
        end_line: int
        chunk_part: int
        total_parts: int
        token_count: int
        imports: list[str]
        calls: list[str]
        parameters: list[str]       # ADDED — needed by summary.py for function/method summaries
        methods: list[str]          # ADDED — needed by summary.py for class summaries
        file_symbols: list[str]     # ADDED — symbol names for file-level chunk summaries
        docstring: str
        summary: str                # filled in by summary.py
        content: str
        embedding: list[float]      # filled in by embedder.py

    Default values for all list fields: field(default_factory=list)
    Default values for str fields that are filled later: ""
    Default values for int fields that are filled later: 0

Field notes:

    parameters   — copied from ParsedSymbol.parameters by chunker.py
                   empty list for class and file chunks

    methods      — copied from ParsedSymbol.methods by chunker.py
                   only populated for chunk_type == "class"
                   empty list for all other chunk types

    file_symbols — only populated for chunk_type == "file"
                   chunker.py fills this with all symbol names from the ParsedFile
                   so summary.py can generate "File: auth.py\nSymbols: verify_token, ..."
                   empty list for all other chunk types

---

## utils/

No changes.

### utils/logger.py

One function. Writes skip/failure events to a list printed in the final report.

    def log_skip(file: str, reason: str, action: str) -> None

Skips log is a module-level list. main.py reads it at the end.

---

### utils/counters.py

    @dataclass
    class PipelineCounters:
        files_discovered:            int = 0
        files_ignored:               int = 0
        files_skipped_unsupported:   int = 0
        files_parsed_ok:             int = 0
        files_parse_failed:          int = 0
        chunks_generated:            int = 0
        embeddings_generated:        int = 0
        embeddings_stored:           int = 0

---

## stages/

---

### stages/loader.py

No changes.

    def load_repository(source: str) -> dict

Input:  string — local path or GitHub URL
Output: dict with repository_name, repository_root, source_type

Logic:
- If source is a local path, verify it exists, return it as-is
- If source is a GitHub URL, clone into TEMP_CLONE_DIR using gitpython
- Raise on clone failure

---

### stages/discovery.py

No changes.

    def discover_files(repository_root: str, counters: PipelineCounters) -> list[FileRecord]

Input:  repo root path, counters
Output: list of FileRecord (all files, unfiltered)

Logic:
- os.walk from repository_root
- Build FileRecord for every file found
- Increment counters.files_discovered for each file

---

### stages/filtering.py

No changes.

    def filter_files(files: list[FileRecord], repo_root: str, counters: PipelineCounters) -> list[FileRecord]

Input:  file list, repo root (to find .gitignore), counters
Output: filtered file list

Logic:
- Pass 1: load .gitignore with pathspec, remove matches
- Pass 2: apply system ignore rules (directories, extensions, patterns)
- Increment counters.files_ignored for every file removed
- Return only files that pass both passes

System ignore rules live as constants inside this file:
IGNORE_DIRS, IGNORE_EXTENSIONS, IGNORE_PATTERNS.

---

### stages/language.py

No changes.

    def detect_languages(files: list[FileRecord], counters: PipelineCounters) -> list[FileRecord]

Input:  filtered file list, counters
Output: same list with language field populated; unsupported files have skipped=True

Logic:
- Look up file.extension in LANGUAGE_MAP
- If found: set file.language
- If not found: set file.skipped=True, file.skip_reason="unsupported_language"
- Log the skip via logger.log_skip
- Increment counters.files_skipped_unsupported
- Return the full list (callers filter on skipped=False)

LANGUAGE_MAP lives as a constant inside this file.
Note on .jsx and .tsx: they map to javascript/typescript here but
parser.py uses different grammars for them. Language detection and
grammar selection are separate concerns.

---

### stages/parser.py

CHANGED — return type is now ParsedFile instead of dict.

    def parse_file(file: FileRecord, counters: PipelineCounters) -> ParsedFile

Input:  single FileRecord, counters
Output: ParsedFile dataclass

Logic:
- Load the correct Tree-Sitter grammar based on file.language and file.extension
- Run parser.parse(source_bytes)
- Walk AST, build one ParsedSymbol per function/class/method found
- For each ParsedSymbol, extract:
    symbol_name, symbol_type, parent_symbol
    start_line, end_line
    parameters (list of param name strings)
    methods (list of method name strings — only for class symbols)
    docstring (first string literal in body, or "")
    calls (list of called function/method names)
- Collect all file-level imports into ParsedFile.imports
- On any exception:
    log failure via logger.log_skip with reason="ast_parse_failed"
    return ParsedFile with parse_status="failed" and empty symbols list
    increment counters.files_parse_failed
- On success: increment counters.files_parsed_ok

Grammar selection inside parser.py:
- file.extension == ".tsx"  → use language_tsx()   from tree-sitter-typescript
- file.extension == ".ts"   → use language_typescript()
- file.extension in (".jsx", ".js") → use JSX-enabled JS grammar
- file.extension == ".py"   → use Python grammar

Using the wrong grammar for .tsx silently mis-parses JSX syntax.
This is why grammar selection is based on extension, not just language.

---

### stages/chunker.py

CHANGED — input type is now ParsedFile instead of dict.
CHANGED — now populates parameters, methods, file_symbols on each Chunk.

    def generate_chunks(parsed: ParsedFile, file: FileRecord) -> list[Chunk]

Input:  ParsedFile, FileRecord
Output: list of Chunk objects (before overflow handling)

Logic:

If parsed.parse_status == "failed":
    - Read entire file content
    - Return one Chunk with:
        chunk_type   = "file"
        symbol_name  = ""
        parameters   = []
        methods      = []
        file_symbols = []   (no symbols were extracted)
        content      = full file text

Otherwise, for each ParsedSymbol in parsed.symbols:
    - Create one Chunk
    - Copy fields directly from ParsedSymbol:
        symbol_name   ← parsed_symbol.symbol_name
        chunk_type    ← parsed_symbol.symbol_type
        parent_symbol ← parsed_symbol.parent_symbol
        start_line    ← parsed_symbol.start_line
        end_line      ← parsed_symbol.end_line
        parameters    ← parsed_symbol.parameters
        methods       ← parsed_symbol.methods
        docstring     ← parsed_symbol.docstring
        calls         ← parsed_symbol.calls
    - Copy file-level fields:
        imports       ← parsed.imports
    - For chunk_type == "file" (parse fallback):
        file_symbols  ← [s.symbol_name for s in parsed.symbols]
    - For all other chunk types:
        file_symbols  = []
    - Read content:
        lines = open(file.path).readlines()
        content = "".join(lines[start_line - 1 : end_line])

chunk_type priority:
    method   → most granular, preferred
    function → one per symbol
    class    → used when class has no individually extracted methods
    file     → fallback only

Does not handle overflow. That is overflow.py's job.

---

### stages/overflow.py

No changes.

    def handle_overflow(chunks: list[Chunk]) -> list[Chunk]

Input:  list of Chunk objects
Output: list of Chunk objects (oversized ones split into multiple parts)

Logic:
- For each chunk, count tokens with tiktoken cl100k_base
- If token_count <= MAX_CHUNK_TOKENS: chunk_part=1, total_parts=1, pass through
- If token_count > MAX_CHUNK_TOKENS:
    split content using sliding window (SLIDING_WINDOW_SIZE lines, SLIDING_OVERLAP lines)
    each window becomes a new Chunk with chunk_part=N, total_parts=total
    new chunks copy all fields from the original, only content and chunk_part differ
- Return flat list with overflow chunks expanded in-place

---

### stages/metadata.py

CHANGED — chunk_id hash now includes parent_symbol.

    def build_metadata(chunk: Chunk) -> Chunk

Input:  Chunk (content + symbol info populated)
Output: same Chunk with chunk_id and token_count filled in

Logic:
- Compute chunk_id:

    For symbol chunks (function, class, method):
        raw = f"{relative_path}::{parent_symbol}::{symbol_name}::{chunk_part}"

    For file-level chunks:
        raw = f"{relative_path}::__file__::{chunk_part}"

    chunk_id = hashlib.sha256(raw.encode()).hexdigest()[:32]

Why parent_symbol is in the hash:
    Without it, UserService.create_user and OrderService.create_user
    produce the same chunk_id. On upsert the second overwrites the first.
    Including parent_symbol makes same-named methods in different classes
    produce different IDs.

    For top-level functions, parent_symbol is an empty string "".
    The hash still works — sha256("src/auth.py::::verify_token::1")
    is distinct from sha256("src/auth.py::UserService::verify_token::1").

- Count tokens with tiktoken cl100k_base, set chunk.token_count
- Return chunk

This is a per-chunk transformation. Caller maps it over the list.

---

### stages/summary.py

CHANGED — now reads parameters, methods, file_symbols from Chunk instead of
reconstructing them from a separate source.

    def generate_summary(chunk: Chunk) -> str

Input:  Chunk (parameters, methods, file_symbols must be populated)
Output: summary string

Logic (no LLM, AST data only):

    chunk_type == "function":
        lines = ["Function: {symbol_name}"]
        if chunk.parameters:
            lines.append("Parameters: {', '.join(parameters)}")
        if chunk.docstring:
            lines.append("Docstring: {docstring}")
        return "\n".join(lines)

    chunk_type == "method":
        lines = ["Method: {symbol_name}"]
        lines.append("Class: {parent_symbol}")
        if chunk.parameters:
            lines.append("Parameters: {', '.join(parameters)}")
        if chunk.docstring:
            lines.append("Docstring: {docstring}")
        return "\n".join(lines)

    chunk_type == "class":
        lines = ["Class: {symbol_name}"]
        if chunk.methods:
            lines.append("Methods: {', '.join(methods)}")
        if chunk.docstring:
            lines.append("Docstring: {docstring}")
        return "\n".join(lines)

    chunk_type == "file":
        lines = ["File: {relative_path}"]
        if chunk.file_symbols:
            lines.append("Symbols: {', '.join(file_symbols)}")
        return "\n".join(lines)

Caller sets chunk.summary = generate_summary(chunk).

---

### stages/embedder.py

No changes.

    def embed_chunks(chunks: list[Chunk], counters: PipelineCounters) -> list[Chunk]

Input:  list of Chunk, counters
Output: same list with embedding field populated on each chunk

Logic:
- Load SentenceTransformer model once at module level, not inside the function
- Build embedding input string per chunk:
    File: {relative_path}
    Language: {language}
    Type: {chunk_type}
    Symbol: {symbol_name}
    Summary: {summary}
    Docstring: {docstring}
    Code:
    {content}
- Collect all input strings, batch in groups of BATCH_SIZE
- Call model.encode(batch, batch_size=BATCH_SIZE, show_progress_bar=True)
- Set chunk.embedding for each chunk
- Increment counters.embeddings_generated
- Return updated list

---

### stages/storage.py

No changes.

    def store_chunks(chunks: list[Chunk], counters: PipelineCounters) -> None

Input:  list of Chunk with embeddings, counters
Output: none (side effect: upserted into Qdrant)

Logic:
- Connect to Qdrant at QDRANT_HOST:QDRANT_PORT
- recreate_collection on every run (local use)
- Build PointStruct per chunk:
    id      = sequential int
    vector  = chunk.embedding
    payload = all metadata fields EXCEPT content and embedding
- Payload must include relative_path (not just file_path)
- Upsert in batches
- Increment counters.embeddings_stored per successful upsert

---

## Data Flow Summary

    main.py
        │
        ├── loader.py          source str          → repo dict
        ├── discovery.py       repo dict           → list[FileRecord]
        ├── filtering.py       list[FileRecord]    → list[FileRecord]     (filtered)
        ├── language.py        list[FileRecord]    → list[FileRecord]     (with language)
        │
        │   for each file where skipped == False:
        ├── parser.py          FileRecord          → ParsedFile           (was: dict)
        ├── chunker.py         ParsedFile          → list[Chunk]          (was: dict)
        ├── overflow.py        list[Chunk]         → list[Chunk]          (overflow expanded)
        │   for each chunk:
        ├── metadata.py        Chunk               → Chunk                (chunk_id + token_count)
        └── summary.py         Chunk               → str                  (sets chunk.summary)
        │
        ├── embedder.py        list[Chunk]         → list[Chunk]          (with embeddings)
        └── storage.py         list[Chunk]         → None                 (upserted to Qdrant)

---

## Import Map

Who imports what. No circular imports.

    main.py         → stages/*, utils/counters, config
    stages/*        → models/*, utils/logger, utils/counters, config
    models/chunk.py → nothing
    models/file.py  → nothing
    models/parsed.py → nothing
    utils/*         → nothing

stages/ files never import from other stages/ files.
models/ files never import from stages/ or utils/.

---

## Rules for All Stage Files

1. Each stage file exports exactly one public function.
2. The function signature is always explicit — no *args, no **kwargs.
3. No stage imports from another stage. Only from models/, utils/, and config.py.
4. No stage touches Qdrant except storage.py.
5. No stage loads the embedding model except embedder.py.
6. No stage calls the Tree-Sitter parser except parser.py.
7. counters is always passed in, never created inside a stage.
8. Logging of skips/failures always goes through utils/logger.py.
9. Grammar selection inside parser.py is based on file.extension, not file.language.
   (.tsx and .ts both have language="typescript" but need different grammars.)
