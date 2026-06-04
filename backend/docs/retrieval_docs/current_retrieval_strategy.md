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
- lexical top-k: `15`
- merged top-k returned: `10`
- max context tokens: `7000`
- max response tokens: `1024`
- dense retrieval enabled by default: `true`
- lexical retrieval enabled by default: `false`
- scored intent enabled by default: `true`
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
3. Query intent and entities are extracted with a bounded scored-intent/entity layer.
4. Search runs across:
   - dense vector search
   - metadata symbol/path search
   - exact entity search for extracted env keys, dependency names, route/API terms, and config keys
   - optional lexical/BM25-style search when enabled
   - dependency search over `calls`
5. Search results are merged, then augmented with:
   - repo-summary and overview candidates for broad repository questions
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
   - deterministic phase-1 flow answer
   - deterministic explanation answer
   - LLM answer
10. Memory is updated with the final answer.

The main orchestrator is `retrieval.main.run_query()`.

## 3. Ingestion Constraints That Shape Retrieval Quality

The current retrieval quality is strongly constrained by ingestion.

### 3.1 Supported source file types

`rag_ingestion/stages/language.py` currently supports:

- `.py`
- `.js`
- `.jsx`
- `.ts`
- `.tsx`
- `.md`
- `.json`
- `.toml`
- `.yml`
- `.yaml`
- `.txt`
- `Dockerfile`
- `.env.example`

Code files still receive the richest AST extraction. Non-code overview/config files are currently ingested as file-level chunks without AST symbols, but selected repo-level files now also receive structured metadata during summary generation.

This still leaves gaps, but the current system can now ingest several files that are often the best evidence for overview questions:

- `README.md`
- `package.json`
- `requirements.txt`
- `pyproject.toml`
- `docker-compose.yml`
- `.env.example`
- many JSON/YAML/TOML files

Important remaining limitation:

- files like lockfiles and ignored secret files are still excluded
- non-code files do not currently contribute imports, calls, or symbols
- config formats are parsed with deterministic lightweight extractors, not full ecosystem-specific parsers
- repo-summary generation and deterministic overview answers consume the first-pass structured metadata fields, but source gating and deeper deterministic answer paths do not yet fully use every field

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
- structured non-code fields such as `file_type`, `summary_facts`, `detected_frameworks`, `dependencies`, `dev_dependencies`, `scripts`, `services`, `ports`, `env_keys`, `entrypoints`, `config_tools`, `build_system`, `volumes`, `service_dependencies`, `base_image`, `workdir`, `package_manager`, `feature_flags`, `provider_keys`, `purpose`, `setup_steps`, `usage_commands`, and `architecture_notes`

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

`retrieval/query_processor.py` classifies the query and extracts entities using bounded heuristics. It still preserves the legacy `intent` field for compatibility, but now also emits a scored intent contract for downstream retrieval and source-gating work.

### 5.1 Legacy intent classes

Legacy intents still emitted:

- `SEMANTIC`
- `DEPENDENCY`
- `SYMBOL`

Classification rules:

- `DEPENDENCY` if the query contains phrases like `calls`, `depends on`, `uses`, `called by`
- `SYMBOL` if the query mentions likely symbols, files, or phrases like `where is`, `show me`, `defined`
- otherwise `SEMANTIC`

### 5.2 Scored intent output

The current scored output includes:

- `primary_intent`
- `intent_scores`
- `entities`
- `is_followup`
- `topic_shift`
- `confidence`

The scored intent families currently emitted are:

- `OVERVIEW`
- `ARCHITECTURE`
- `TECH_STACK`
- `EXPLANATION`
- `SYMBOL`
- `FILE`
- `TRACE`
- `DEPENDENCY`
- `CONFIG`
- `CODE_REQUEST`
- `FOLLOWUP`
- `LOW_CONTEXT`
- `SEMANTIC`

`RETRIEVAL_ENABLE_SCORED_INTENT=0` disables the richer scoring and exact-entity extraction logic, but the processor still emits the same output shape populated from the legacy intent and empty rich entity lists. This avoids forcing search, assembly, or later answer builders to handle two incompatible query contracts.

### 5.3 Entity extraction

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
- uppercase env/config keys such as `CODESEEK_DATABASE_URL`
- route-like paths such as `/api/v1/health`
- route/API terms such as `submission-key`
- dependency/model/library tokens such as `qdrant-client` and `BAAI/bge-small-en-v1.5`
- known bare dependency names such as `fastapi`, `uvicorn`, `qdrant`, and `pytest`

This stage is still rule-based. There is no learned intent classifier and no structural parser for the query itself. The important change is that the rules are now bounded by a documented output contract instead of being open-ended one-off routing checks.

## 6. Retrieval Stage

`retrieval/searcher.py` is the main search implementation.

### 6.1 Dense vector search

Dense retrieval:

- loads `SentenceTransformer(BAAI/bge-small-en-v1.5)`
- is enabled by default with `RETRIEVAL_ENABLE_DENSE=1`
- can be disabled with `RETRIEVAL_ENABLE_DENSE=0` for offline lexical/metadata evals
- encodes `query: <raw_query>`
- queries Qdrant for top `15` by default
- uses payload plus vector similarity score

This remains the primary semantic retrieval layer in the current system.

### 6.2 Optional lexical search

The searcher now has a feature-flagged in-process BM25-style lexical layer.

Current behavior:

- disabled by default with `RETRIEVAL_ENABLE_LEXICAL=0`
- enabled with `RETRIEVAL_ENABLE_LEXICAL=1`
- builds lazily per Qdrant `collection_name` on first lexical query
- caches the lexical index in process memory
- invalidates the cache after successful session ingestion for that collection
- indexes relative path, symbol names, qualified symbols, chunk type, language, signature, docstring, summary, bounded content excerpt, imports, calls, parameters, methods, and file symbols

This layer is intentionally in-process for the first implementation so it adds no new deployment dependency. Multi-worker deployments still need to tolerate per-worker cache rebuilds until a shared sparse-index strategy is adopted.

### 6.3 Metadata search

Metadata search supplements dense retrieval with exact-match filters over:

- `relative_path`
- `qualified_symbol`
- `symbol_name`

There are also a few hardcoded path-hint heuristics for disambiguation, for example:

- websocket/ws-related paths
- test-related paths

Direct symbol/path matches are treated as exact evidence. Broader path-hint metadata matches are treated as probabilistic ranking signals.

### 6.4 Exact entity search

Exact entity search consumes the richer entity output from `query_processor.py`.

Current exact entity categories:

- `env_keys`
- `dependencies`
- `config_keys`
- `routes`
- `api_terms`
- `exact_terms`

Current behavior:

- runs even when lexical retrieval is disabled
- scans a bounded set of stored Qdrant payloads for exact entity matches
- prefers structured metadata fields such as `env_keys`, `dependencies`, `dev_dependencies`, `detected_frameworks`, `services`, `entrypoints`, `summary_facts`, `routes`, and `api_terms`
- falls back to exact matching against `relative_path`, `symbol_name`, `qualified_symbol`, `summary`, and bounded `content_excerpt`
- returns matches as source type `exact_entity`
- marks matches as `exact_retrieval_hit` during merge so they are promoted ahead of dense/lexical/probabilistic metadata hits

This is the first bridge between query understanding and retrieval ranking. It improves exact env/config/dependency/API lookup without requiring lexical retrieval to be enabled by default.

### 6.5 Dependency search

For `DEPENDENCY` intent, the searcher also queries Qdrant for chunks whose `calls` array contains the requested symbol.

This allows questions like:

- who uses `x`
- where is `y()` called

### 6.6 Merge strategy

Search results are merged by `chunk_id`.

Properties of the current merge:

- dense similarity score is kept as `retrieval_score`
- dense, lexical, and broad metadata matches contribute to `fusion_score`
- a boolean `multi_layer_hit` is added when the chunk appeared in more than one layer
- exact dependency, direct symbol, direct file/path, and exact entity hits are promoted ahead of probabilistic matches
- merged results are sorted by exact hit, multi-layer hit, dense score, fusion score, then lexical overlap

This keeps graph/entity-backed evidence ahead of probabilistic dense or lexical matches while still allowing lexical retrieval to improve recall for exact wording, config keys, dependency names, and doc-heavy queries.

## 7. Search Augmentations

After the base merge, the current system applies two important augmentations.

### 7.1 Overview candidate injection

For broad overview queries, `_inject_overview_candidates()` pulls extra chunks from Qdrant by scrolling the collection and ranking them with `_overview_priority()`.

The current priority function first favors the synthetic repo-summary artifact:

- `__repo_summary__.md`

It then favors paths that look like:

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

Current behavior:

The ranking logic can now surface the synthetic repo-summary chunk and representative repo files. Backend re-ingestion/eval validation passed for the first repo, but broader multi-repo validation is still needed before treating the rule-based summary as sufficient.

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

This logic can now prefer the synthetic `__repo_summary__.md` artifact and first-pass structured metadata from README, dependency manifests, Docker Compose, Dockerfile, and env examples. It is still not a deep semantic architecture model, so broad architecture answers need more dedicated assembly work.

### 12.3 Flow mode

Triggered by `is_flow_explanation_request()`.

Signals include a phase-1 flow marker plus backend/session/indexing terms, such as:

- `backend request orchestration`
- `auth session lifecycle`
- `indexing session creation flow`
- `trace indexing`

Behavior:

- routes through `build_flow_answer()`
- covers phase-1 deterministic families:
  - backend request orchestration
  - auth/session lifecycle
  - indexing/session creation trace
- maps each flow family to reusable evidence roles instead of tuning for exact user wording
- selects up to seven flow-relevant sources with required roles first
- returns those selected flow evidence sources to the API, keeping UI source cards aligned with the deterministic answer body
- computes evidence state as `strong`, `partial`, or `weak`
- renders ordered implementation steps from matched roles such as auth entrypoint, session creation, session lookup, logout/session deletion, indexing job, and retrieval pipeline
- reports missing required evidence roles when the selected source set is partial or weak
- includes a key-evidence section and source list
- emits `response_mode=flow_summary`
- bypasses the LLM

Important limitation:

This is intentionally bounded to phase-1 flow families. Deployment/configuration, provider credential lifecycle, and broad architecture implementation answers still need later deterministic or LLM-assisted handling.

### 12.4 Explanation mode

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

### 16.1 The ingestion corpus is still code-heavy

This is the highest-impact problem.

Many of the best files for:

- project overview
- tech stack
- deployment shape
- architecture
- configuration

are now partially indexed, receive first-pass structured metadata, and are synthesized into a rule-based repo-summary artifact. They still do not go through AST extraction.

As a result, the retrieval layer has better access to repo-level evidence than before, but broad project understanding is still weaker than symbol-level code understanding.

### 16.2 Overview heuristics still need richer structured evidence

The searcher and deterministic overview code both try to prioritize:

- `README`
- `package.json`
- `requirements.txt`
- `docker-compose.yml`
- config files

The index now contains many of these files and stores first-pass structured metadata for dependency groups, services, ports, env keys, entrypoints, and README purpose/setup sections. Downstream answer quality is still limited because source gating and non-overview deterministic answer builders do not yet fully consume those fields.

### 16.3 Query understanding is improved but still heuristic

Query understanding now emits a scored intent/entity contract and extracts env keys, dependency names, route/API terms, config keys, files, and symbols. Exact entity promotion uses those extracted terms before probabilistic ranking.

Remaining predictable failure modes:

- broad semantic questions can be misread as symbol-level questions
- service names are not reliable until structured non-code metadata exists
- architecture intent is detected but not yet routed through a dedicated architecture assembly path
- follow-up rewriting is based on shallow markers, not discourse understanding
- topic-shift behavior is specified in the plan but not implemented as an entity-memory flow yet

### 16.4 Lexical retrieval is first-pass and still experimental

The code now includes an optional in-process BM25-style lexical layer, but it is still a first-pass implementation and disabled by default. Remaining limitations:

- cache invalidation is process-local
- multi-worker deployments can rebuild indexes independently
- tuning still depends on broader eval coverage
- weighted fusion is intentionally deferred until baselines exist

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

### 17.1 Highest priority: deepen non-code repository evidence

The baseline support now exists, but the next step is to deepen it for:

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

Deepening this layer will further improve:

- project overview
- tech stack answers
- deployment explanations
- architecture summaries

more than prompt tuning will.

### 17.2 Validate and tune lexical retrieval

The first lexical layer now exists. Next work is validation and tuning:

- run retrieval evals with `RETRIEVAL_ENABLE_LEXICAL=0` and `RETRIEVAL_ENABLE_LEXICAL=1`
- use `RETRIEVAL_ENABLE_DENSE=0` for offline lexical/metadata evals when the embedding model is not cached locally
- add exact-wording evals for env keys, config keys, dependency names, and README phrases
- measure memory and latency cost of lazy per-collection indexing
- tune fusion only after baselines exist

Initial baseline results are recorded in [Lexical Retrieval Baseline Results](./eval_results_lexical_baseline.md). The first run showed that lexical retrieval should remain disabled by default because it did not improve `hit@10` and reduced MRR on the exact-wording eval.

The later scored-intent/exact-entity promotion pass improved the default dense path on the same exact-wording eval from `hit@10 0.500` to `0.750` without enabling lexical retrieval. That makes structured extraction the better next step than enabling BM25 by default.

After structured non-code metadata extraction and re-ingestion, the backend collection contains `753` chunks from `122` parsed files. The exact-wording eval remained at `hit@10 0.750` with lexical disabled, improved `expected_framework_score` to `1.000`, and still showed lexical-enabled MRR below the lexical-off default path. Lexical should therefore remain disabled by default.

After repo-summary artifact re-ingestion, the backend collection contains `763` chunks from `123` parsed files. The lexical-off exact-wording eval stayed stable at `hit@10 0.750`, `mrr@10 0.383`, `expected_framework_score 1.000`, and `expected_dependency_score 0.875`.

The refreshed collection now includes the synthetic `__repo_summary__.md` chunk. Overview retrieval and deterministic overview answers prefer that chunk over ordinary README/package/config chunks. Incremental ingestion refreshes unchanged repo-summary evidence files so the synthetic summary is not rebuilt from only the changed-file subset.

The multi-repo eval suite now uses committed fixture repos for frontend-heavy, backend-heavy, infra-heavy, and mixed/monorepo shapes, plus CodeSeek exact-wording and phase-1 flow regressions. The latest lexical-off run passed thresholds with `24` cases, weighted `hit@10 0.917`, weighted `mrr@10 0.712`, weighted citation coverage `0.937`, expected response-mode score `1.000`, and expected framework/dependency scores of `1.000`.

The phase-1 flow eval verifies `flow_summary` routing, citations, answer terms, varied auth wording, and deterministic latency. Latest flow-only metrics are `4` cases, `hit@10 1.000`, `mrr@10 1.000`, citation coverage `1.000`, response-mode score `1.000`, answer-term score `1.000`, latency p50 `155 ms`, and latency p95 `168 ms`.

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
3. Re-ingest and evaluate the repo-summary chunk across representative repos.
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
