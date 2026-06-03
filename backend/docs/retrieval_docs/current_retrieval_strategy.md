# Current Retrieval, Argumentation, and Prompting Strategy

This document describes the retrieval and answer-generation pipeline exactly as it exists in the current backend implementation. It is not a target design. It is a code-based snapshot of the present system so the strategy can be reviewed, challenged, and improved.

Primary implementation files:

- `retrieval/main.py`
- `retrieval/query_processor.py`
- `retrieval/searcher.py`
- `retrieval/expander.py`
- `retrieval/assembler.py`
- `retrieval/source_filter.py`
- `retrieval/code_answers.py`
- `retrieval/llm.py`
- `retrieval/memory.py`
- `rag_ingestion/stages/language.py`
- `rag_ingestion/stages/chunker.py`

## 1. Current Libraries and Models

Backend libraries currently used in the retrieval path:

- `qdrant-client==1.15.1`
- `sentence-transformers==5.1.0`
- `tiktoken==0.11.0`
- `tree-sitter==0.25.2`
- `tree-sitter-python==0.25.0`
- `tree-sitter-javascript==0.25.0`
- `tree-sitter-typescript==0.23.2`
- `httpx==0.28.1`
- `fastapi==0.116.1`
- `uvicorn==0.35.0`
- `prometheus-client==0.21.1`
- `psycopg[binary]==3.2.9`
- `cryptography==45.0.4`
- `pathspec==0.12.1`
- `gitpython==3.1.43`
- `requests==2.32.3`
- `groq==0.31.1`

Current embedding model:

- `BAAI/bge-small-en-v1.5`
- embedding dimension: `384`
- query prefix: `query: `

Current LLM provider defaults:

- Groq: `llama-3.3-70b-versatile`
- OpenAI: `gpt-4o-mini`
- OpenRouter: `openai/gpt-4o-mini`
- Gemini: `gemini-2.5-flash`

Current default runtime knobs from `retrieval/config.py`:

- dense top-k: `15`
- merged top-k returned: `10`
- max context tokens: `7000`
- max response tokens: `1024`
- call expansion enabled: `true`
- parent expansion enabled: `true`
- split-part expansion enabled: `true`
- sibling expansion enabled: `false`
- call expansion limit: `5`
- conversation history turns: `5`

## 2. High-Level Pipeline

At a high level, a query currently goes through this sequence:

1. API receives the query and resolves session/thread/provider context.
2. Retrieval memory is loaded and may be used to rewrite short follow-up queries.
3. Query intent and entities are extracted with regex heuristics.
4. Search runs across:
   - dense vector search
   - metadata symbol/path search
   - dependency search over `calls`
5. Search results are merged, then augmented with:
   - overview candidates for broad repo-summary questions
   - import-backed candidates for section/data questions
6. Expansion pulls in related chunks:
   - split parts
   - parent class
   - callees for dependency tracing
7. Context is assembled under a token budget.
8. Display-time source filtering reduces the visible evidence set.
9. Response mode is selected:
   - deterministic code answer
   - deterministic overview answer
   - deterministic explanation answer
   - LLM answer
10. Memory is updated with the final answer.

The main orchestrator is `retrieval.main.run_query()`.

## 3. Ingestion Constraints That Shape Retrieval Quality

The current retrieval quality is strongly constrained by ingestion.

### 3.1 Supported source file types

`rag_ingestion/stages/language.py` currently supports only:

- `.py`
- `.js`
- `.jsx`
- `.ts`
- `.tsx`

Everything else is marked `unsupported_language` and skipped from normal parsing/indexing.

This means the current system does not natively ingest many files that are often the best evidence for overview questions:

- `README.md`
- `requirements.txt`
- `pyproject.toml`
- `docker-compose.yml`
- `.env.example`
- `tailwind.config.*`
- `vite.config.*`
- JSON config not already included through some other path

### 3.2 Chunking behavior

`rag_ingestion/stages/chunker.py` currently produces:

- symbol-level chunks when parsing succeeds and symbols exist
- one file-level chunk when parsing succeeds but no symbols exist
- one file-level chunk when parsing fails

Stored chunk metadata can include:

- `relative_path`
- `chunk_type`
- `symbol_name`
- `parent_symbol`
- `signature`
- `start_line`
- `end_line`
- `imports`
- `calls`
- `parameters`
- `methods`
- `docstring`
- `content`

This is important because most retrieval behavior depends on symbol names, import lists, and call graphs extracted here.

## 4. Request Entry and Memory Handling

The end-to-end retrieval request starts in `retrieval/main.py`.

### 4.1 Memory models

There are three memory implementations in `retrieval/memory.py`:

- `ConversationMemory`
- `SessionConversationMemory`
- `ThreadConversationMemory`

All three store:

- original query
- final answer
- resolved query

The history block format is plain text:

- `--- CONVERSATION SUMMARY ---` if a rolling summary exists
- `--- CONVERSATION HISTORY ---`
- `Q1: ...`
- `A1: ...`
- `--- END HISTORY ---`

Older turns are summarized by truncating answers and keeping a compact rolling list.

### 4.2 Follow-up query rewriting

Short or vague follow-ups are resolved against the previous query in `retrieval.main._resolve_query_info()`.

Rewrite happens only when:

- there is prior memory
- the current query has no extracted symbols or files
- the query is short or contains follow-up markers such as:
  - `also`
  - `same`
  - `more`
  - `details`
  - `it`
  - `that`
  - `this`

When rewriting is triggered, the previous resolved query is prepended to the current query and reprocessed. This is a simple concatenation strategy, not a semantic rewrite model.

## 5. Query Understanding

`retrieval/query_processor.py` classifies the query and extracts entities using regexes.

### 5.1 Intent classes

Current intents:

- `SEMANTIC`
- `DEPENDENCY`
- `SYMBOL`

Classification rules:

- `DEPENDENCY` if the query contains phrases like `calls`, `depends on`, `uses`, `called by`
- `SYMBOL` if the query mentions likely symbols, files, or phrases like `where is`, `show me`, `defined`
- otherwise `SEMANTIC`

### 5.2 Entity extraction

Current entity extraction pulls:

- snake_case identifiers
- CamelCase identifiers
- explicit backticked identifiers
- `name()` call patterns
- explicit file references ending in:
  - `.py`
  - `.js`
  - `.ts`
  - `.tsx`
  - `.jsx`

This stage is entirely rule-based. There is no learned intent classifier and no structural parser for the query itself.

## 6. Retrieval Stage

`retrieval/searcher.py` is the main search implementation.

### 6.1 Dense vector search

Dense retrieval:

- loads `SentenceTransformer(BAAI/bge-small-en-v1.5)`
- encodes `query: <raw_query>`
- queries Qdrant for top `15` by default
- uses payload plus vector similarity score

This is the only semantic retrieval layer in the current system.

### 6.2 Metadata search

Metadata search supplements dense retrieval with exact-match filters over:

- `relative_path`
- `qualified_symbol`
- `symbol_name`

There are also a few hardcoded path-hint heuristics for disambiguation, for example:

- websocket/ws-related paths
- test-related paths

This layer is exact and heuristic-driven. It is not a BM25 or fuzzy lexical search.

### 6.3 Dependency search

For `DEPENDENCY` intent, the searcher also queries Qdrant for chunks whose `calls` array contains the requested symbol.

This allows questions like:

- who uses `x`
- where is `y()` called

### 6.4 Merge strategy

Search results are merged by `chunk_id`.

Properties of the current merge:

- dense similarity score is kept as `retrieval_score`
- a boolean `multi_layer_hit` is added when the chunk appeared in more than one layer
- merged results are sorted with multi-layer hits first, then by descending dense score

This means exact metadata matches can be promoted if they overlap with dense results, but pure metadata hits have no independent ranking score beyond merge position and later reranking.

## 7. Search Augmentations

After the base merge, the current system applies two important augmentations.

### 7.1 Overview candidate injection

For broad overview queries, `_inject_overview_candidates()` pulls extra chunks from Qdrant by scrolling the collection and ranking them with `_overview_priority()`.

The current priority function favors paths that look like:

- `README.md`
- `package.json`
- `requirements.txt`
- `pyproject.toml`
- `.env` or `.env.example`
- `docker-compose.yml`
- `vite.config.*`
- `tailwind.config.*`
- app entrypoints such as `src/main.*`, `src/App.*`, `main.py`
- data files and symbols named like `app`, `home`, `skills`, `about`, `contact`

Important limitation:

The ranking logic knows these files are useful, but if ingestion never indexed them, they still cannot be returned. This is the single biggest mismatch in the current overview strategy.

### 7.2 Import-backed candidate injection

`_inject_import_backing_candidates()` looks at the first few candidate chunks and tries to resolve named imports whose identifiers overlap with the query.

Current behavior:

- only named JS/TS-style imports are parsed
- supports relative imports and `@/` aliases
- resolves `.ts`, `.tsx`, `.js`, `.jsx`, and `index.*`
- fetches matching exported symbol chunks from the imported file

This is useful for questions like:

- explain the skills section
- where does this rendered data come from

Important limitation:

This mechanism does not currently handle:

- Python imports
- default imports
- namespace imports
- re-export chains
- JSON/YAML/config imports

## 8. Reranking

After augmentation, `_rerank_with_query_tokens()` applies a small lexical boost.

The boost uses token overlap against:

- `relative_path`
- `symbol_name`
- `qualified_symbol`
- `summary`

This is not a full reranker. It is a lightweight lexical bias added on top of merge ordering and dense score.

## 9. Expansion Stage

`retrieval/expander.py` attaches structurally related chunks.

### 9.1 Expansion types

Current expansion types:

- `primary`
- `split_part`
- `parent_class`
- `callee`

### 9.2 Expansion rules

Split-part expansion:

- if a chunk has `total_parts > 1`, fetch all chunks with the same file and symbol

Parent expansion:

- if the chunk is a method with `parent_symbol`, fetch the enclosing class chunk

Callee expansion:

- only enabled for `DEPENDENCY` intent
- inspects `calls` from candidate chunks
- fetches up to `CALL_EXPANSION_LIMIT` target symbols

There is a config flag for sibling expansion, but it is not currently implemented in this file.

## 10. Context Assembly

`retrieval/assembler.py` converts selected chunks into the final LLM context.

### 10.1 Budgeting

Token counting uses `tiktoken` with `cl100k_base`.

Budget logic:

- start from `MAX_CONTEXT_TOKENS`
- subtract tokens used by history block
- fill the remaining budget with ranked context blocks

### 10.2 Ranking order before assembly

Chunks are ordered by:

1. expansion tier
2. descending retrieval score
3. path
4. line number

Expansion tier priority:

- `primary`
- `split_part`
- `parent_class`
- `callee`

### 10.3 Block format

Each context block contains:

- file path
- symbol
- chunk type
- line range
- expansion label when not primary
- signature when present
- summary when present
- first few call targets when present
- raw excerpt text

### 10.4 Truncation

Primary chunks can be truncated to fit the remaining budget. Non-primary chunks are skipped if they do not fit.

## 11. Source Filtering and Evidence Gating

`retrieval/source_filter.py` decides which sources are shown to the user and which sources are allowed to be mentioned by the LLM.

### 11.1 Query-sensitive filtering

The filter detects:

- test queries
- compound trace queries
- auth-flow trace queries
- overview queries

It then:

- separates primary vs expanded sources
- prefers non-test sources unless the query asks for tests
- scores sources by lexical overlap with the query
- applies caps to reduce noise

Current caps:

- primary: typically `5`, `6`, or `7`
- expanded: typically `2` or `3`

### 11.2 Trace-anchor injection

For certain auth or request-flow questions, the filter can force specific symbols into the displayed evidence set, such as:

- `account_info`
- `authenticated_get`
- `signed_params`
- `sign_query`
- `auth_headers`

This is a targeted heuristic for trace-style questions.

### 11.3 Why this matters

The filtered sources are not just display hints. They directly constrain later answer generation because the LLM prompt includes a strict allowed-source list.

## 12. Response Mode Routing

Before any LLM call, `retrieval/main.py` decides whether to answer deterministically.

### 12.1 Code mode

Triggered by `retrieval.code_answers.is_code_request()`.

Signals include phrases like:

- `show code`
- `code snippet`
- `full code`

Behavior:

- formats exact source excerpts from the preferred retrieved sources
- may add supporting imported exports
- returns snippets directly
- bypasses the LLM

### 12.2 Overview mode

Triggered by `is_overview_request()`.

Signals include phrases like:

- `what is this project about`
- `tech stack`
- `architecture overview`

Behavior:

- selects up to five overview-priority sources
- tries project summary from:
  - `README`
  - `package.json`
  - chunk summaries
- extracts tech stack from:
  - `package.json`
  - `requirements.txt`
  - `pyproject.toml`
  - Vite/Tailwind config
  - `docker-compose.yml`
- emits architecture bullets from visible file types
- bypasses the LLM

Important limitation:

This logic can only see those files if they were retrieved or are readable locally through returned source paths. Because many of those files are not currently ingested, overview mode is often starved of the right evidence.

### 12.3 Explanation mode

Triggered by `is_explanation_request()`.

Signals include phrases like:

- `explain this code`
- `walk me through`
- `detailed explanation`

Behavior:

- chooses one main source
- renders a direct sentence about file/symbol
- adds bullets for:
  - render source
  - backing data
  - interaction/behavior
  - concrete values
  - source coverage
- may inspect imported exported arrays/objects for labels and titles
- bypasses the LLM

This mode is currently strongest for frontend component explanation where named exported data arrays are nearby and JS/TS imports are conventional.

### 12.4 Low-context fallback

If source filtering yields no shown sources, the answer is:

`Insufficient context in retrieved code to answer confidently. Try naming a file, symbol, component, route, or config file.`

## 13. LLM Prompting Strategy

When the query is not handled by deterministic answer builders, `retrieval/llm.py` constructs the prompt.

### 13.1 System prompt

The current system prompt instructs the model to:

- use only provided code context
- avoid outside knowledge
- avoid proposing new code unless asked
- return exactly `Insufficient context in retrieved code to answer confidently.` when required evidence is missing
- be concise and technical
- avoid claims not visible in context
- mention only files and symbols present in allowed sources
- start with a one-line direct answer
- follow with `3-6` short bullet points
- avoid code blocks unless code was explicitly requested

It also instructs negative answers to use wording like:

- `Not found in retrieved context.`

### 13.2 User prompt construction

The user-side prompt is assembled in this order:

1. history block, if any
2. response-mode instruction block, if the query looks like code / overview / explanation
3. strict allowed-sources block
4. code context block
5. extra context blocks from supporting imports
6. final `Question: ...`

### 13.3 Response-mode prompt variants

The prompt text changes based on the query:

- code mode asks for the smallest complete snippet and allows `1-2` code blocks
- overview mode asks for project purpose, tech stack, runtime shape, and concrete technologies
- explanation mode asks for render structure, data sources, map/loop behavior, layout/styling, and handlers

### 13.4 Allowed-source restriction

When allowed sources exist, the prompt includes a strict list:

- `relative_path :: symbol_name (lines start-end)`

Then it adds:

- `You must only reference files/symbols from ALLOWED SOURCES. If other code appears in context, ignore it.`

This is the current argumentation guardrail. It narrows hallucination risk, but it also means the model cannot synthesize beyond the filtered source set even when broader assembled context exists.

### 13.5 Provider call shape

All providers are called through OpenAI-compatible chat completion endpoints using:

- one `system` message
- one `user` message
- `temperature=0.1`
- `max_tokens=MAX_RESPONSE_TOKENS`

## 14. Current Argumentation Strategy

The system does not have a separate formal argumentation engine. The current argumentation strategy is an implicit evidence-gated synthesis pipeline.

In practice, the argument is constructed through these layers:

1. retrieve candidate chunks
2. expand to related chunks
3. assemble context with line-labeled blocks
4. prune visible/allowed sources
5. either:
   - generate a deterministic summary from those sources, or
   - force the LLM to answer only from those sources

This gives the system three major guardrails:

- context must come from indexed chunks
- visible citations are capped and pruned
- the LLM is explicitly forbidden from referencing anything outside the allowed-source list

The tradeoff is that if the right evidence is missing, the system does not degrade gracefully into a good repo-level summary. It instead becomes over-constrained and can answer about the retrieval system itself or about whichever code chunks were easiest to retrieve.

## 15. Current Strengths

The current strategy is reasonably strong at:

- symbol lookup
- direct file/method location
- short dependency traces using `calls`
- grounded code snippets
- frontend explanation when data exports are locally imported and named
- preventing broad hallucinations through strict evidence gating

## 16. Current Weaknesses

These are the main weaknesses visible in the current implementation.

### 16.1 The ingestion corpus is too code-only

This is the highest-impact problem.

Many of the best files for:

- project overview
- tech stack
- deployment shape
- architecture
- configuration

are not being indexed at all because the language detector only supports Python and JS/TS source files.

As a result, the retrieval layer often has no direct access to the strongest overview evidence.

### 16.2 Overview heuristics are smarter than the corpus

The searcher and deterministic overview code both try to prioritize:

- `README`
- `package.json`
- `requirements.txt`
- `docker-compose.yml`
- config files

but this strategy is undercut by ingestion gaps. The ranking knows what matters, but the index often does not contain it.

### 16.3 Query understanding is brittle

Intent classification and entity extraction are regex-only. This causes predictable failure modes:

- broad semantic questions can be misread as symbol-level questions
- file detection is limited to code extensions
- dependency phrasing is narrow
- follow-up rewriting is based on shallow markers, not discourse understanding

### 16.4 Metadata search is exact-match only

There is no true sparse lexical retrieval layer such as BM25. That means:

- symbol aliases are weakly handled
- text-heavy questions over summaries/config/docs are weak
- dense retrieval has to do too much work alone

### 16.5 Import-backed evidence is narrow

Current import-following works mainly for named JS/TS imports. It misses many common repo patterns.

### 16.6 The allowed-source gate can be too tight

Strict allowed-source prompting reduces hallucination risk, but it can also reduce answer quality when:

- source filtering dropped a useful chunk
- assembled context contains helpful support not listed as allowed
- the user asks a broad repo question that needs more than five sources

### 16.7 Deterministic answer builders are domain-specific

The explanation builder is optimized for component/data-export cases. It is less general for:

- backend orchestration
- infra/config flows
- multi-file service traces

## 17. Second Opinion: What To Improve First

If the goal is better response quality, the current best next steps are clear.

### 17.1 Highest priority: ingest non-code repository evidence

Add first-class ingestion support for:

- `README.md`
- `package.json`
- `requirements.txt`
- `pyproject.toml`
- `docker-compose.yml`
- `.env.example`
- `tailwind.config.*`
- `vite.config.*`
- key YAML/JSON/TOML config files

Recommended approach:

- treat these as structured file-summary chunks, not unsupported files
- store parsed metadata in payload fields
- keep raw excerpt content for direct citation

This single change will improve:

- project overview
- tech stack answers
- deployment explanations
- architecture summaries

more than prompt tuning will.

### 17.2 Add a true lexical retrieval layer

Add BM25 or equivalent sparse retrieval over:

- file content
- chunk summaries
- paths
- symbol names

Then fuse:

- dense search
- sparse search
- metadata filters

This will materially improve:

- exact wording queries
- docs/config questions
- tech-stack questions
- path-sensitive questions

### 17.3 Build a repository-summary document during ingestion

Instead of deriving overview answers only at query time, generate a compact repo summary artifact during ingestion:

- repo purpose
- entrypoints
- frameworks
- key services
- config/deployment files

Store it as one or more high-priority chunks. This gives overview queries a stable, high-signal retrieval target.

### 17.4 Broaden import and dependency understanding

Extend the current support-following logic to handle:

- Python imports
- default imports
- namespace imports
- re-exports
- config/data files
- service wiring patterns

For backend repos, also consider indexing:

- route -> service -> db dependencies
- module import graphs

### 17.5 Relax the answer gate carefully

Keep evidence grounding, but consider two layers instead of one:

- `display_sources`: tight list for user-facing citation display
- `reasoning_sources`: broader list allowed for synthesis

That will let the LLM use a slightly wider evidence set without citing everything.

### 17.6 Improve query rewriting

Current follow-up resolution is cheap and sometimes useful, but shallow. Improve it by:

- carrying forward the previous subject explicitly
- storing previous cited symbols/files
- resolving pronouns against recent entities instead of concatenating raw text

### 17.7 Add evaluation focused on broad semantic questions

The system has retrieval docs and regression tests, but response-quality evaluation should explicitly include:

- project overview
- tech stack
- architecture
- where data comes from
- startup/deployment flow
- session creation to indexing

across multiple repo shapes:

- frontend-only
- backend-only
- monorepo
- infra-heavy repo

## 18. Practical Response-Quality Upgrade Plan

If improving answer quality is the near-term goal, the best order is:

1. Index non-code overview/config files.
2. Add sparse lexical retrieval and merge it with dense retrieval.
3. Create a repo-summary chunk during ingestion.
4. Expand import/dependency tracing beyond named JS/TS imports.
5. Widen reasoning sources while keeping displayed citations selective.
6. Add quality eval sets for overview and architecture questions.
7. Only then revisit prompt tuning.

Prompt tuning alone will not solve the current overview failures because the main problem is missing evidence, not missing instruction quality.

## 19. Bottom Line

The current system is a guarded code-retrieval pipeline with deterministic shortcuts and a tightly constrained LLM fallback. It is strongest on grounded symbol-level questions and weakest on broad repository understanding.

The core issue is not the LLM prompt. The core issue is that the retrieval corpus and retrieval layers are still optimized for code symbols more than repository understanding.

If the system needs materially better answers to questions like:

- what is this project about
- tech stack
- architecture overview
- how does this app work end to end

the next step should be better ingestion and retrieval coverage for repo-level evidence, not more prompt complexity.
