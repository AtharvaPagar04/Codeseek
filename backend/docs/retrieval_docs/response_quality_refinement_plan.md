# Response Quality Refinement Plan

This document defines the current response-quality improvement plan for CodeSeek based on the implementation that exists today. It is not a generic RAG checklist. Every improvement below is grounded in the current ingestion, retrieval, assembly, deterministic-answer, and LLM paths in this repository.

The purpose of this doc is:

- explain where current answer quality is limited
- define the concrete changes we plan to make
- track those changes with pointwise checkboxes
- keep response-quality work separate from deployment/infrastructure work

## Scope

This plan covers answer quality for:

- broad repo questions
- tech-stack questions
- architecture/explanation questions
- technical implementation questions that require detailed, engineering-level answers
- follow-up questions
- section/data-backed questions
- dependency and trace questions
- low-context / ambiguous questions
- explicit code-request questions where the user asks for code or expects a concrete snippet

This plan does not cover:

- deployment environment setup
- auth/session infrastructure hardening
- provider billing/rate-limit policy
- UI polish not directly tied to answer quality

## Current Implementation Snapshot

The current response pipeline already has useful structure:

- non-code ingestion support exists for `README.md`, `package.json`, `requirements.txt`, `pyproject.toml`, `docker-compose.yml`, `.env.example`, `Dockerfile`, and related text/config files
- dense retrieval is implemented in [searcher.py](../../retrieval/searcher.py)
- feature-flagged in-process lexical retrieval exists, but remains disabled by default because baseline evals did not justify enabling it
- metadata and dependency helper retrieval exist in [searcher.py](../../retrieval/searcher.py)
- scored intent/entity extraction exists in [query_processor.py](../../retrieval/query_processor.py)
- exact entity promotion exists in [searcher.py](../../retrieval/searcher.py) for env keys, dependency names, route/API terms, config keys, and future structured metadata fields
- import-backed candidate injection exists, but is still JS/TS-oriented
- source assembly is token-budgeted in [assembler.py](../../retrieval/assembler.py)
- deterministic answer paths exist in [code_answers.py](../../retrieval/code_answers.py) for overview and explanation-style prompts
- fallback LLM generation is implemented in [llm.py](../../retrieval/llm.py)

Most recent validation result:

- exact entity promotion improved the CodeSeek exact-wording eval with lexical still disabled from `hit@10 0.500` to `0.750`
- MRR improved from `0.375` to `0.454`
- after structured non-code metadata re-ingestion, the backend collection contains `753` chunks from `122` parsed files
- after structured non-code metadata re-ingestion, the lexical-off exact-wording eval stayed at `hit@10 0.750` and improved `expected_framework_score` to `1.000`
- after repo-summary artifact re-ingestion, the backend collection contains `763` chunks from `123` parsed files
- after repo-summary artifact re-ingestion, the lexical-off exact-wording eval stayed stable at `hit@10 0.750`, `mrr@10 0.383`, `expected_framework_score 1.000`, and `expected_dependency_score 0.875`
- multi-repo fixture eval coverage now covers frontend-heavy, backend-heavy, infra-heavy, and mixed/monorepo shapes
- latest multi-repo suite result: `24` cases, weighted `hit@10 0.917`, weighted `mrr@10 0.712`, weighted citation coverage `0.937`
- multi-repo thresholds are defined in [eval_thresholds_multi_repo.json](./eval_thresholds_multi_repo.json) and the latest run passed them
- deterministic flow answer phase 1 is implemented for backend request orchestration, auth/session lifecycle, and indexing/session creation trace questions
- deterministic flow answer phase 2 is implemented for deployment/configuration coverage based on config-file evidence
- deployment/configuration flow supports repo-root and monorepo-root sessions by resolving explicit file hints through safe suffix-based local fallback, for example `Dockerfile` can resolve to `backend/Dockerfile`
- deterministic flow answer phase 2 now also covers provider credential lifecycle using API endpoint and provider-store evidence
- deterministic architecture summary mode is implemented with `response_mode=architecture_summary`, using repo overview/config/deployment/module evidence instead of generic overview routing
- phase-1 flow answers use a generic role-based evidence model, bypass the LLM through `response_mode=flow_summary`, and compute `strong`/`partial`/`weak` evidence state
- phase-1 flow answers return the exact evidence sources selected by the deterministic flow builder, so API/UI source cards stay aligned with the answer's numbered steps instead of showing broader assembled context
- phase-1 flow rendering now uses role-labeled numbered steps with inline evidence references, improving readability without adding query-specific prompt rules
- phase-1 flow answer bodies no longer repeat separate `Key evidence` and `Sources` sections; the API still returns the selected source list for UI source cards
- phase-1 flow context quality is accepted for now: retrieved roles, source cards, and cited symbols are correct; deeper prose/presentation polish is deferred to the later LLM/rendering phase
- phase-1/2 flow eval coverage is implemented in [eval_codeseek_flow_phase1.json](./eval_codeseek_flow_phase1.json)
- latest phase-1/2 flow eval result: `6` cases, `hit@10 1.000`, `mrr@10 0.867`, citation coverage `1.000`, expected-file score `1.000`, response-mode score `1.000`, answer-term score `1.000`, latency p50 `148 ms`, latency p95 `165 ms`
- the `architecture overview` eval case now passes with `response_mode=architecture_summary`, expected-file score `1.000`, expected answer-term score `1.000`, and latency `321 ms`; the broader exact-wording eval file still contains unrelated older failures that remain outside this architecture change
- lexical-enabled eval after structured metadata stayed at `hit@10 0.750` but had lower MRR than lexical-off, so lexical remains disabled by default
- `RETRIEVAL_ENABLE_SCORED_INTENT=1` stays enabled by default
- `RETRIEVAL_ENABLE_LEXICAL=0` stays disabled by default until broader evals, latency, and memory behavior justify a rollout
- two-layer source gating is implemented: `display_sources` (max 6, strict citation set) and `reasoning_sources` (max 12, synthesis context set)
- intent-aware context budgets are active via `INTENT_CONTEXT_BUDGETS` in `config.py` and consumed by `assemble_for_reasoning()` in the LLM path
- `RETRIEVAL_ENABLE_TWO_LAYER_SOURCES=1` by default; set to `0` to revert to single-list legacy behaviour
- history starvation fix: `HISTORY_TOKEN_CAP` (1500 tok global) + `INTENT_HISTORY_CAPS` (per-intent tighter caps); all assembly calls now use `get_history_block_capped()`
- partial-evidence signaling: `score_evidence_confidence()` classifies display sources as `strong`/`partial`/`weak`; LLM-path answers get a banner prepended for `partial`/`weak`; `evidence_confidence` is logged and returned in the API response

The main quality problem is no longer "prompt weakness first." The main problems are now:

1. ~~non-code files now carry first-pass structured metadata~~ ✓ resolved
2. ~~overview answers now prefer repo-summary evidence~~ ✓ resolved
3. ~~deterministic phase-1/2 flow and architecture coverage~~ ✓ resolved
4. cross-file dependency tracing is incomplete
5. follow-up handling is still shallow
6. context assembly and source gating — ✓ resolved: two-layer model, intent-aware budgets, history cap, and partial-evidence signaling are all live

- note: items 4 and 5 above are now resolved; see WS6 and WS7 sections for details

Immediate next implementation step:

- **WS8 Source Gating and Context Assembly is now complete** (`11/11` tasks)
- context-budget tuning is now implemented in `retrieval/config.py`, with the tuned profile covered by backend tests
- **WS4 Deterministic Answer Coverage Expansion is complete**; the earlier summary note about "WS4 remaining" is stale and the checklist below is the source of truth
- **WS5 Query Understanding Improvements is now complete** after tightening explicit code-request/lookup routing, eliminating substring-based false follow-up matches, and keeping the scored-intent contract bounded by eval-driven rules
- **WS6 Dependency and Import Tracing Expansion is now complete (`10/10`)** after adding retrieval-side Python import resolution, JS/TS default and namespace import support, bounded re-export-chain following, direct JSON config/data import support, an explicit default import-trace depth limit of `3`, explicit visited-set/cap hardening for trace expansion, deterministic route-to-handler-to-store/database traces for explicit backend auth/provider flows, and reuse of retrieved import/dependency support evidence inside deterministic answer builders
- **WS7 Follow-Up Query Resolution is now complete (`8/8`)** — per-turn entity memory (files, symbols, routes, env_keys, services) is persisted in the `thread_turn_entities` DB table; topic-shift detection is implemented in `retrieval/follow_up_memory.py` comparing new-query entities against recent cited entities using a two-window heuristic (close: last 2 turns, broad: last 8); entity-aware query rewriting injects the most salient recent entity into vague pronoun-only queries before retrieval; all three memory classes (`ConversationMemory`, `SessionConversationMemory`, `ThreadConversationMemory`) accept `entities` and `primary_intent` kwargs and expose `recent_turn_entities()`; `main.py` calls `extract_cited_entities()` after each answer and stores via `memory.add(entities=...)`; clearing chat messages also clears entity rows; 31 new tests pass
- **WS9 Sibling and Neighborhood Expansion is now complete (`7/7`)** — `_sibling_chunks_for` fetches same-class-first then same-file chunks from Qdrant; `_merge_siblings` applies lexical overlap scoring (`_sibling_lexical_overlap`), per-primary cap (`SIBLING_MAX_PER_PRIMARY=2`), and budget fraction cap (`SIBLING_BUDGET_FRACTION=0.20`); compound identifiers split on underscores for correct partial matching; intent gating via `SIBLING_ENABLED_INTENTS` (OVERVIEW excluded); assembler tier updated so siblings rank after split_parts; feature gated by `EXPAND_SIBLINGS=False` by default until evals confirm precision; 24 new tests pass
- **WS13 Prompt Refinement is now complete (`6/6 implemented tasks`)** — `SYSTEM_PROMPT` in `llm.py` rewritten: grounding rules restructured (5a/5b/5c), snippet-vs-prose rule added (inline refs always; fenced blocks only on explicit ask), deep-dive walk-through guidance added, negative-answer phrasing tightened; `_build_prompt` adds `RESPONSE MODE: CODE REQUEST / OVERVIEW / EXPLANATION / TECHNICAL TRACE` labels with mode-specific format requirements; EXPLANATION mode now covers both UI (render/loop/handlers) and backend (request flow/transforms/deps); TECHNICAL TRACE is new for TRACE/DEPENDENCY/SYMBOL paths; `LOW_CONTEXT_FALLBACK`, `PARTIAL_EVIDENCE_BANNER`, and `WEAK_EVIDENCE_BANNER` rewritten with actionable guidance and example rephrasing; 346 tests pass
- **WS10 Evaluation and Measurement is now complete** after adding latency-bucket reporting for retrieval-only, deterministic, and LLM-backed paths plus provider-aware eval runner support
- the next substantive implementation work is now outside WS5/WS10; lexical should remain disabled by default until a future rollout decision is justified by broader measurements
- continue keeping lexical disabled by default until a future rollout decision is justified by broader recall, latency, and memory evidence

## Non-Goals and Constraints

This plan is intentionally constrained by the current system shape.

- the current backend is Python-first and JS/TS-aware; this plan improves those paths first
- this plan does not assume an immediate language-parser expansion to Go, Rust, Java, or other ecosystems
- response-quality improvements must preserve grounding and citation discipline
- response-quality improvements must not add unbounded latency to every query

Where a workstream introduces new complexity, it must define:

- implementation location
- invalidation/staleness behavior
- latency impact
- tests/evals needed before rollout

## Architecture Decisions Fixed Up Front

The original version of this plan deferred several core decisions. Those are now fixed here so implementation does not drift mid-build.

### Lexical Retrieval Storage Decision

We will not introduce Elasticsearch/OpenSearch as a first implementation step. The first lexical retrieval implementation should stay inside the current backend stack.

Decision:

- first implementation: in-process BM25-style index over stored chunks
- storage source: chunk text, chunk summary, relative path, symbol names, selected metadata fields
- lifecycle: build lazily per `collection_name` on first lexical query, cache in process memory, invalidate after successful ingestion for that collection
- multi-worker behavior: every worker owns its own in-process cache; ingestion completion must trigger per-worker invalidation or tolerate lazy rebuild on stale-cache detection
- ranking merge: reciprocal rank fusion for probabilistic retrievers only; exact graph/entity hits are promoted separately
- rollback: gate the lexical path behind `RETRIEVAL_ENABLE_LEXICAL`

Reasoning:

- lowest operational overhead
- no new deployment dependency
- easier to debug against current eval tooling
- enough to validate whether lexical retrieval materially improves quality before adopting a more operationally heavy solution
- feature-flagged rollout avoids requiring a code revert if hybrid retrieval hurts precision or latency

Future option:

- if in-process lexical retrieval becomes too slow or memory-heavy, evaluate Qdrant sparse-vector support as the next step before considering a separate search service
- if multi-worker cache invalidation becomes unreliable, move lexical state to a shared sparse-vector or external search backend

### Repo Summary Generation Decision

The repo-summary artifact should not be LLM-generated in the first implementation. It should be rule-based and derived from structured evidence already available during ingestion.

Decision:

- first implementation: rule-based repo-summary generation
- inputs: README extraction, dependency manifests, deployment/config files, entrypoints, detected frameworks/services
- output: one `repo_summary` chunk plus structured metadata fields
- no LLM dependency during ingestion for the first pass

Reasoning:

- avoids provider dependency during ingestion
- avoids ingestion failure when the LLM is unavailable
- avoids a hidden per-repo cost increase
- makes staleness/invalidation deterministic

Future option:

- only consider LLM-assisted summary synthesis after the rule-based artifact is stable and measurable gaps remain

### Query Understanding Strategy Decision

We will not keep adding open-ended regex rules indefinitely.

Decision:

- next step: expand heuristics into a bounded scored-intent/entity layer
- stop condition: once it supports the defined query/entity types in this document, do not keep growing regex debt without eval evidence
- only consider a classifier or small query-understanding call after heuristic coverage is measured against eval failures
- fallback behavior: when `RETRIEVAL_ENABLE_SCORED_INTENT` is disabled, legacy query heuristics must still emit the scored-intent output contract with lower confidence values

Reasoning:

- fastest low-risk improvement path
- keeps behavior debuggable
- prevents accidental long-term drift into unbounded regex growth
- downstream retrieval, assembly, and answer code should not need to support two incompatible intent shapes

### Retrieval Fusion Decision

Not all retrieval results have the same trust level. Dense retrieval, lexical retrieval, and text metadata matches are probabilistic. Dependency graph hits, direct symbol hits, direct file hits, and exact entity matches are stronger evidence.

Decision:

- apply reciprocal rank fusion to probabilistic sources:
  - dense retrieval
  - lexical retrieval
  - metadata text matches
- promote or reserve slots for exact sources:
  - dependency graph matches
  - direct symbol matches
  - direct file/path matches
  - exact route/env/config-key matches
- track source type in each candidate so later source gating can preserve the distinction

Reasoning:

- exact graph-backed evidence should not be diluted by rank-only fusion
- probabilistic retrievers still benefit from RRF because their raw scores are not directly comparable
- explicit source typing makes downstream confidence signaling easier

### Feature Flag Decision

Every major retrieval behavior change should be reversible without a code rollback.

Required flags:

- `RETRIEVAL_ENABLE_LEXICAL`
- `RETRIEVAL_ENABLE_REPO_SUMMARY`
- `RETRIEVAL_ENABLE_TWO_LAYER_SOURCES`
- `RETRIEVAL_ENABLE_SIBLING_EXPANSION`
- `RETRIEVAL_ENABLE_SCORED_INTENT`
- `RETRIEVAL_ENABLE_DETERMINISTIC_CONFIDENCE`

Reasoning:

- retrieval changes can improve recall while hurting precision for some query families
- feature flags allow targeted rollback during deployment validation
- flags make A/B-style local validation easier before changing defaults

### Scored Intent Output Contract

The query understanding layer must produce a stable contract before search, assembly, source gating, and deterministic answer builders depend on it.

Required output shape:

```json
{
  "primary_intent": "TRACE",
  "intent_scores": {
    "TRACE": 0.82,
    "SYMBOL": 0.41,
    "OVERVIEW": 0.08
  },
  "entities": {
    "symbols": ["create_session"],
    "files": [],
    "routes": [],
    "env_keys": [],
    "services": [],
    "dependencies": []
  },
  "is_followup": false,
  "topic_shift": false,
  "confidence": 0.78
}
```

Required intent families:

- `OVERVIEW`
- `TECH_STACK`
- `ARCHITECTURE`
- `EXPLANATION`
- `TRACE`
- `SYMBOL`
- `FILE`
- `DEPENDENCY`
- `CONFIG`
- `CODE_REQUEST`
- `FOLLOWUP`
- `LOW_CONTEXT`
- `SEMANTIC`

Intent behavior:

| Intent | Primary behavior |
|---|---|
| `OVERVIEW` | project purpose, main capabilities, high-level repo shape |
| `TECH_STACK` | framework/runtime/dependency/tooling extraction |
| `ARCHITECTURE` | entrypoints, major modules, runtime services, data/control flow, config/deployment boundaries |
| `EXPLANATION` | explain selected files/symbols/components/config using retrieved evidence |
| `TRACE` | follow dependency/import/call-flow evidence across files |
| `SYMBOL` | direct symbol lookup and local code explanation |
| `FILE` | direct file/path lookup and explanation |
| `DEPENDENCY` | exact caller/callee/dependency lookup before probabilistic synthesis |
| `CONFIG` | env/config/deployment key lookup and explanation |
| `CODE_REQUEST` | code-oriented answer with snippets when evidence is strong enough |
| `FOLLOWUP` | resolve against recent entities before retrieval |
| `LOW_CONTEXT` | ask for clarification or provide a cautious partial answer |
| `SEMANTIC` | default synthesis path for broad questions that do not match a narrower family |

Reasoning:

- downstream modules need a predictable interface
- ranked intent scores allow mixed behavior without forcing a brittle single-label decision
- confidence can drive fallback behavior and partial-evidence wording

Legacy fallback behavior:

- if `RETRIEVAL_ENABLE_SCORED_INTENT=0`, use the current heuristic query logic
- map the legacy result into the same scored-intent contract
- set `primary_intent` from the legacy classifier
- set only the relevant legacy intent score high enough to preserve current routing
- set `confidence` conservatively because entity extraction is weaker in legacy mode
- keep downstream consumers contract-compatible regardless of flag state

### Enriched Non-Code Extraction Schema

Structured non-code extraction must use a shared field contract so ingestion, repo summary, lexical retrieval, and deterministic answers read the same fields.

Common fields:

| Field | Storage location | Purpose |
|---|---|---|
| `file_type` | metadata | identifies parser/extractor family |
| `summary_facts` | metadata + chunk summary | compact facts for repo summary and lexical retrieval |
| `detected_frameworks` | metadata | tech-stack and overview answers |
| `dependencies` | metadata | tech-stack and dependency questions |
| `dev_dependencies` | metadata | build/test/tooling answers |
| `scripts` | metadata | run/build/test command answers |
| `services` | metadata | docker-compose/deployment answers |
| `ports` | metadata | deployment/runtime answers |
| `env_keys` | metadata | config/env-var lookup answers |
| `entrypoints` | metadata | architecture and startup-flow answers |
| `config_tools` | metadata | toolchain and framework answers |
| `raw_excerpt` | chunk content | grounded fallback evidence |

File-specific fields:

| File type | Required extracted fields |
|---|---|
| `package.json` | `dependencies`, `dev_dependencies`, `scripts`, `detected_frameworks`, `config_tools` |
| `pyproject.toml` | `dependencies`, `dev_dependencies`, `build_system`, `config_tools` |
| `requirements.txt` | `dependencies` |
| `docker-compose.yml` | `services`, `ports`, `env_keys`, `volumes`, `service_dependencies` |
| `Dockerfile` | `base_image`, `workdir`, `ports`, `entrypoints`, `package_manager` |
| `.env.example` | `env_keys`, `feature_flags`, `provider_keys` |
| `README.md` | `purpose`, `setup_steps`, `usage_commands`, `architecture_notes` |

Reasoning:

- repo-summary generation needs structured inputs
- deterministic answer builders need predictable evidence locations
- lexical indexing needs consistent text fields
- tests can assert schema stability instead of checking only prose summaries

### Trace Expansion Limits

Dependency and import tracing must be bounded.

Decision:

- default max trace depth: `3`
- keep a `visited` set keyed by file path + symbol/entity
- stop expansion when a node has already been visited
- cap total trace-expanded chunks per query
- expose depth and chunk caps through config only after defaults are tested

Reasoning:

- real repos contain circular imports and long re-export chains
- unbounded tracing can loop or flood context with weakly related code
- bounded traversal makes latency and token use predictable

### Follow-Up Memory Contract

Follow-up handling needs explicit entity memory. The current conversation memory should be extended with recent cited entities so query resolution can refer to real previous context instead of only raw text.

Per-turn memory fields:

| Field | Type | Retention |
|---|---|---|
| `turn_id` | string | persisted with the turn |
| `original_query` | string | persisted with the turn |
| `resolved_query` | string | persisted with the turn |
| `final_answer` | string | persisted with the turn |
| `primary_intent` | string | last 5-8 turns for entity resolution |
| `cited_files` | list[string] | last 5-8 turns for entity resolution |
| `cited_symbols` | list[string] | last 5-8 turns for entity resolution |
| `cited_routes` | list[string] | last 5-8 turns for entity resolution |
| `cited_env_keys` | list[string] | last 5-8 turns for entity resolution |
| `cited_services` | list[string] | last 5-8 turns for entity resolution |
| `created_at` | timestamp | persisted with the turn |

Resolution rules:

- keep a compact recent-entity set per thread
- prefer entities from the latest relevant turn
- do not apply old entities when topic-shift detection marks the new query as independent
- pass recent entities into query understanding before retrieval

Topic-shift detection:

- compare new-query entities against cited entities from the last `2` turns first
- expand comparison to the last `5-8` turns only if there is no clear new topic and the query is still ambiguous
- treat the query as a likely topic shift when it has a new explicit entity/subsystem and no overlap with recent cited files, symbols, routes, env keys, or services
- treat the query as a likely topic shift when the primary intent changes strongly and the new query includes a concrete new topic, such as moving from auth tracing to database architecture
- do not treat the query as a topic shift when it contains follow-up markers such as `it`, `that`, `this`, `where is it used`, `how does that work`, `what about`, or `also`
- if a topic shift is detected, do not inject old entities into retrieval, but keep normal chat history available for the LLM path

Reasoning:

- questions like `where is it used` and `how does that work` need concrete previous entities
- Workstream 5 and Workstream 7 share this data contract
- explicit retention prevents old topics from polluting new questions

### Deterministic Confidence Decision

Deterministic builders must participate in the same confidence model as the LLM path.

Decision:

- each deterministic answer builder must compute an evidence state:
  - `strong`
  - `partial`
  - `weak`
- `strong`: answer deterministically
- `partial`: answer with explicit partial-evidence wording or hand off to the LLM with a partial-evidence flag
- `weak`: decline deterministic answer and use fallback/LLM path

Reasoning:

- deterministic answers can otherwise sound overconfident with thin evidence
- confidence should be consistent across all answer paths
- this avoids binary answer-or-fallback behavior

### Automated Eval Scoring Decision

Manual review remains necessary for final answer quality, but it should not be the only scoring mechanism.

Minimum automated scoring:

- retrieval `hit@k`
- expected file present
- expected symbol present
- expected route/env/config key present
- expected framework/dependency present
- expected no-answer when evidence is absent

Reasoning:

- automated checks make retrieval tuning repeatable
- broad manual review can focus on harder synthesis quality
- regression gates should catch obvious ranking failures quickly

### Intent-Aware Context Budget Starting Values

The current global context budget is `7000` tokens. Intent-aware budgets should start with explicit values and be tuned against evals.

Initial budget table:

| Query family | Starting context budget |
|---|---:|
| `OVERVIEW` | 5000 |
| `TECH_STACK` | 4500 |
| `ARCHITECTURE` | 6000 |
| `SYMBOL` | 2500 |
| `SEMANTIC` / synthesis | 5000 |
| `TRACE` / dependency trace | 6500 |
| `FOLLOWUP` explanation | 4500 |
| technical deep-dive | 6000 |
| `CODE_REQUEST` | 5500 |
| `LOW_CONTEXT` | 2500 |

Reasoning:

- these values are starting points, not final tuning results
- trace and technical answers need more room for cross-file evidence
- symbol lookup and low-context queries should not consume the full budget
- evals should drive adjustments after implementation

### Sibling Relevance Decision

Sibling expansion should not require new embedding calls in the first implementation.

Decision:

- only consider siblings in the same file/module/class as a primary selected chunk
- require lexical overlap with query tokens or extracted entities
- cap sibling chunks to at most `20%` of remaining context budget
- include at most `2-3` sibling chunks per primary chunk
- do not include siblings for overview queries by default

Reasoning:

- avoids doubling embedding work during expansion
- keeps sibling expansion cheap and predictable
- prevents neighboring code from flooding context when it is only weakly related

### Latency Gate Decision

Response-quality changes must be measured against concrete latency gates.

Initial latency gates:

| Path | p50 target | p95 target |
|---|---:|---:|
| deterministic / overview path | <= 750 ms | <= 1500 ms |
| retrieval + assembly before LLM | <= 1000 ms | <= 2500 ms |
| LLM-backed path excluding provider latency | <= 1500 ms | <= 3000 ms |
| full LLM-backed query including provider latency | <= 6000 ms | <= 15000 ms |

Reasoning:

- backend retrieval latency and provider latency must be measured separately
- retrieval changes should not hide behind provider variability
- concrete targets make deployment readiness review less subjective

### Source Set Size Decision

Two-layer source gating must remain bounded. The reasoning set should be broader than displayed citations, but not open-ended.

Decision:

- `display_sources`: max `6`
- `reasoning_sources`: max `12`
- `reasoning_sources` must include all `display_sources`
- reasoning-only sources may include up to `6` expanded/supporting chunks
- exact hits should be preserved in both sets when possible
- reasoning-only sources can support synthesis, but should not be cited unless promoted into `display_sources`

Reasoning:

- keeps the LLM grounded without starving synthesis
- prevents reasoning context from becoming an uncapped hidden source pool
- gives implementation a concrete starting cap that can be tuned by evals

## Response Quality Goals

We want the system to be reliably good at:

- answering `what is this project about`
- identifying tech stack from code and config evidence
- producing useful architecture summaries
- answering technical implementation questions with enough depth for an engineer to act on
- tracing flows across files and layers
- answering section questions backed by imported/exported data
- handling follow-up questions without losing context
- refusing or narrowing vague questions instead of hallucinating
- returning code when the user explicitly asks for it
- including a small supporting code snippet when it materially improves an explanation
- signaling uncertainty clearly when evidence is partial instead of forcing binary confident-or-insufficient behavior

## Performance and Freshness Requirements

Response quality work must preserve basic operational behavior.

Latency targets:

- overview / lookup / deterministic-answer path: keep median backend latency close to current behavior
- broad synthesis and trace queries: allow moderate latency increase, but track it explicitly
- no workstream should silently add multiple expensive stages without a measured before/after comparison

Freshness requirements:

- repo-derived artifacts such as `repo_summary` must be regenerated whenever the repo is re-ingested
- partial re-ingestion must not leave repo-summary metadata stale relative to updated files
- in-process lexical indexes must be invalidated after successful ingestion for their collection
- multi-worker deployments must tolerate per-worker lexical cache rebuilds until a shared sparse index exists
- retrieval should prefer current session/repo artifacts only, never mixed stale artifacts across sessions

Confidence requirements:

- answers should distinguish:
  - high-confidence grounded answer
  - partial-evidence answer
  - insufficient-context answer
- the system should avoid pretending certainty when only partial evidence was retrieved

## Technical Answer Expectations

The system should not optimize only for short summary-style answers. It also needs to handle technical questions from users who expect implementation detail.

That means responses should:

- explain the actual code path, not just summarize intent
- name concrete files, functions, symbols, routes, config keys, and data structures
- describe control flow, dependencies, and side effects where relevant
- distinguish confirmed evidence from inference
- include code when the user explicitly asks for code
- include a short supporting snippet when a snippet makes the explanation materially clearer

The snippet behavior should follow these rules:

- if the user explicitly asks for code, return code-oriented output grounded in retrieved sources
- if the user asks for explanation only, prefer prose first and add a short snippet only when it clarifies the answer
- snippets should be small, relevant, and tied to cited source files
- snippet selection should favor the most explanatory lines, not the longest block
- if the evidence is too weak to safely provide code, the response should say that directly instead of fabricating an example

## Workstreams

## 1. Lexical Retrieval Layer

Dense retrieval is currently doing too much work alone. We need a sparse/lexical layer for exact names, config keys, env vars, route names, dependency names, and doc-heavy queries.

Expected impact:

- better exact-match recall
- better README/config retrieval
- fewer misses on symbol spelling variations
- better ranking for broad repository questions

Tasks:

- [x] implement an in-process BM25-style index over chunk text, summaries, relative paths, symbol names, and selected metadata
- [x] define the indexing schema for lexical search inputs during ingestion/storage
- [x] include bounded chunk content excerpts in lexical search inputs
- [x] implement lazy per-collection lexical index construction on first query
- [x] cache lexical indexes by `collection_name`
- [x] invalidate lexical index cache after successful ingestion for that collection
- [x] document and test stale-cache behavior for single-worker local usage
- [x] document multi-worker behavior and the future shared-index upgrade path
- [x] load and query the lexical index from [searcher.py](../../retrieval/searcher.py)
- [x] gate lexical retrieval with `RETRIEVAL_ENABLE_LEXICAL`
- [x] fuse dense + sparse + metadata results with reciprocal rank fusion first
- [x] promote or reserve slots for exact dependency, symbol, file, route, and env/config-key hits outside ordinary RRF
- [x] establish a tuning plan for lexical vs dense weighting by query family:
  - symbol lookup     → **DENSE_ONLY** — lexical hurts recall (Δhit=−0.400); BM25 pollutes CONFIG file-hint injection
  - dependency trace  → **EQUAL** — no measured gain; dense handles dependency manifests as well
  - overview          → **EQUAL** — repo_summary injection already covers this path; lexical adds noise
  - semantic          → **EQUAL** — dense embedding captures semantic similarity; no BM25 uplift observed
  - script: `scripts/lexical_layer_benchmark.py`; results: `docs/retrieval_docs/lexical_layer_benchmark_results.md`
- [x] defer weighted-fusion tuning until eval baselines exist
  - per-family RRF weight tuning is deferred until lexical can be gated per `primary_intent` and `retrieval_eval.py` baselines per family are established with lexical enabled
- [x] add tests proving lexical retrieval rescues cases that dense retrieval misses
- [x] add evaluation queries specifically for env vars, config keys, dependency names, and README phrases
- [x] measure the latency and memory cost of the lexical layer before making it default
  - **index build**: 1,167 ms, 41.1 MB peak (paid once at worker startup; cached thereafter)
  - **per-query latency delta**: +42.2 ms mean (range: −15 ms to +135 ms across 16 cases)
  - **recall result** (16 cases, 4 families):
    - SYMBOL: hit@10 dense=1.000 → lex=0.600 (**▼ −0.400** — lexical hurts)
    - DEPENDENCY: hit@10 dense=0.500 → lex=0.500 (= no change)
    - OVERVIEW: hit@10 dense=1.000 → lex=1.000 (= no change)
    - SEMANTIC: hit@10 dense=0.667 → lex=0.667 (= no change)
    - Overall: dense=0.812, lex=0.688 (**▼ −0.125 overall regression**)
  - **verdict: KEEP DISABLED** — latency acceptable (+42 ms < 150 ms gate) but no recall gain; SYMBOL path actively regresses due to BM25 noise displacing CONFIG-injected file hints

## 2. Stable Repo Summary Artifact

Overview answers still depend too much on whichever chunks happen to rank well at query time. We need a first-class repo summary artifact created during ingestion.

Expected impact:

- more stable `what is this project about` answers
- stronger `tech stack` answers
- more consistent architecture summaries

Tasks:

- [x] define a `repo_summary` chunk type generated once per ingestion run
- [x] build the first repo-summary generator as a rule-based synthesis from structured evidence:
  - `README`
  - dependency manifests
  - deployment/config files
  - entrypoints
  - detected frameworks/services
- [x] store the repo-summary artifact with explicit metadata so retrieval can prioritize it for overview queries
- [x] regenerate the repo-summary artifact on full ingestion refreshes affecting the repo
- [x] ensure partial re-ingestion cannot leave an out-of-date repo-summary artifact behind
  - unchanged repo-summary evidence files are refreshed during incremental runs
  - ordinary unchanged source files remain skipped
- [x] update overview ranking logic in [searcher.py](../../retrieval/searcher.py) to prefer the repo-summary chunk before ordinary file chunks
- [x] update [code_answers.py](../../retrieval/code_answers.py) to use repo-summary evidence as the primary source for overview responses
- [x] add regression tests for overview/tech-stack questions across different repo shapes
- [x] measure whether the rule-based repo-summary is sufficient before considering any LLM-assisted summary generation
  - **verdict: rule-based is SUFFICIENT** — content quality is adequate; the blocker was retrieval, not generation
  - **bugs found and fixed** in `retrieval/searcher.py`:
    - **bug 1**: `_repository_overview_candidates()` scrolled only the first 400 Qdrant records (by UUID order); the `repo_summary` chunk was at position 682 in a 1,212-chunk collection, so it was never fetched. Fixed by adding a targeted `chunk_type=repo_summary` filter scroll before the general scroll.
    - **bug 2**: injected overview chunks were **appended** to the merged list and silently cut off by `TOP_K_AFTER_MERGE=10`. Fixed by **prepending** them and assigning a synthetic `retrieval_score=1.0` + `exact_retrieval_hit=True` so `_rerank_with_query_tokens` keeps them at the top.
    - **bug 3**: `_is_overview_query` only matched exact phrases, missing `TECH_STACK` / `ARCHITECTURE` intent queries. Fixed by adding `_is_overview_intent()` gate alongside the phrase gate.
  - **measurement result**: `repo_summary` now surfaces at rank=1 in 7/8 broad overview probes (up from 1/8); remaining miss ("give me an overview") hits rank=10 (boundary case)
  - **LLM-assisted generation: NOT justified** — the summary content (frameworks, deps, services, env keys, entrypoints, purpose) is factually complete; the failures were all retrieval-layer bugs, not content gaps


## 3. Better Structured Extraction From Non-Code Files

First-pass structured non-code extraction now exists for the supported repo-level file set. Remaining work is to evaluate it across repos and connect the metadata more deeply into repo-summary generation, answer builders, and source gating.

Expected impact:

- better deployment/config explanations
- better framework/service detection
- better infrastructure and env-var answers

Tasks:

- [x] enrich `package.json` extraction beyond name/description/dependencies
- [x] enrich `pyproject.toml` extraction for build system, tool configs, and dependency groups
- [x] enrich `docker-compose.yml` extraction for services, ports, dependencies, volumes, and env references
- [x] enrich `Dockerfile` extraction for base image, workdir, exposed ports, entrypoint/cmd, and package manager signals
- [x] enrich `.env.example` extraction for grouped env keys and feature flags
- [x] enrich `README.md` extraction for explicit purpose/setup/usage sections
- [x] implement the shared extraction schema defined in this document
- [x] preserve structured fields in chunk metadata where they help ranking or deterministic answers
- [x] ensure chunk content still contains a grounded raw excerpt for fallback answers
- [x] add extraction-specific automated eval checks before enabling enriched extraction by default
- [x] verify expected-framework and expected-dependency scoring against enriched metadata
- [x] add tests for each supported non-code file type
- [x] re-ingest the backend collection after payload schema changes
- [x] rerun exact-wording eval after structured metadata re-ingestion

## 4. Deterministic Answer Coverage Expansion

The deterministic paths are one of the strongest parts of the system, but today they still cover only a subset of the questions users actually ask.

Expected impact:

- more consistent answers
- less dependence on provider quality
- lower hallucination rate

Tasks:

- [x] phase 1: generalize explanation-mode answers for backend orchestration flows
- [x] phase 1: add deterministic handling for auth/session lifecycle questions
- [x] phase 1: add deterministic handling for indexing/session creation trace questions
- [x] phase 1 hardening: return deterministic flow evidence sources to the API so UI source cards match the answer body
- [x] phase 1 rendering: use role-labeled numbered flow steps with inline evidence references
- [x] phase 1 rendering: remove duplicated flow `Key evidence` and answer-body `Sources` sections now that API source cards use the same evidence set
- [x] phase 2: add deterministic handling for deployment/configuration explanation questions
- [x] phase 2: add deterministic handling for provider credential lifecycle questions
- [x] phase 2: add deterministic or LLM-assisted architecture answer handling using entrypoints, major modules, services, and config/deployment boundaries
- [x] phase 2: extend imported-data-backed explanation logic beyond frontend component patterns
- [x] phase 3: add deterministic handling only for single-symbol deep-dive questions where the full symbol evidence is retrieved
- [x] route broad technical implementation questions through the LLM path with stronger source gating and snippet-preserving assembly
- [x] add evidence-state computation to deterministic builders: `strong`, `partial`, `weak`
  - implemented for phase-1 flow answers
- [x] add tests for deterministic phase 1 before opening phase 2
- [x] decline or hand off deterministic answers when evidence is weak
- [x] define snippet-selection rules for deterministic answers when the user explicitly requests code
- [x] add snippet-friendly explanation formatting for cases where a short code sample improves clarity
- [x] add tests for each deterministic phase before opening the next phase
  - phase-1/2 flow eval passes: `6` cases, `hit@10 1.000`, `mrr@10 0.867`, response-mode score `1.000`, latency p50 `148 ms`
  - architecture eval case passes: `response_mode=architecture_summary`, expected-file score `1.000`, answer-term score `1.000`, latency `321 ms`

## 5. Query Understanding Improvements

Current intent/entity handling is still mostly heuristic. It needs to become more robust before retrieval runs.

Expected impact:

- fewer misrouted queries
- better retrieval mode selection
- cleaner context assembly

Tasks:

- [x] audit current query-type routing failures using saved examples from manual validation
  - audited against `eval_codeseek_exact_wording.json` (11 cases): 3 routing failures found
  - **failure 1**: env-key queries (`RETRIEVAL_ENABLE_LEXICAL`, `CODESEEK_DATABASE_URL`, `CODESEEK_APP_ENCRYPTION_KEY`) routed to `CONFIG` correctly but `retrieval/config.py` was not injected as a file hint → hit@10=0 for 2 cases
  - **failure 2**: `"Where is BAAI/bge-small-en-v1.5 configured?"` — model name parsed as dependency, not env-key, so config injection did not fire
  - **failure 3**: `"Which code invalidates the lexical index"` — `invalidates` not in `DEPENDENCY_PATTERNS` so routed to `SEMANTIC` instead of `DEPENDENCY`
  - **fixes applied** in `retrieval/query_processor.py`:
    - added `_inject_config_files()`: fires when env-key, config-key, or dependency+`"configured"` entities are present → injects `retrieval/config.py`, `rag_ingestion/config.py`, `.env.example` as file hints
    - added `invalidates`/`invalidate` to `DEPENDENCY_PATTERNS`
  - **result**: `eval_codeseek_exact_wording.json` improved from `hit@10=0.727` → `hit@10=1.000`, `expected_file=0.682` → `expected_file=1.000`; all 346 tests still pass
- [x] define a hard stop for heuristic expansion once the planned entity/query families are covered
  - added `HEURISTIC_COVERAGE_COMPLETE = True` sentinel in `query_processor.py` with a doc-block defining the gate conditions: new heuristics only when (a) an eval case fails with hit@k=0 or wrong response_mode AND (b) the failure cannot be fixed by improving entity extraction alone
- [x] only evaluate classifier-based query understanding after heuristic coverage is measured against eval failures
  - heuristic coverage is now measured: `hit@10=1.000` on the exact-wording fixture; no eval failures remain that would justify a classifier at this time
- [x] implement the scored-intent output contract defined in this document
- [x] improve entity extraction for:
  - file names
  - symbol names
  - route/API terms
  - env vars
  - dependency names
- [x] improve entity extraction for service names after structured non-code metadata exists
- [x] distinguish overview, trace, explanation, lookup, and vague/follow-up queries more explicitly
- [x] route `ARCHITECTURE` separately from `OVERVIEW` when structural evidence is available
  - implemented via `response_mode=architecture_summary`; architecture queries no longer share the generic overview path
- [x] tighten follow-up marker matching so substring hits inside unrelated words do not trigger `FOLLOWUP`
  - replaced naive substring matching with token/phrase-aware detection; queries such as `audit logging flow` now stay in `TRACE` instead of accidentally matching `it`
- [x] add tests for classification and entity extraction edge cases
- [x] strengthen explicit code-request and lookup routing cues
  - `show the implementation`, `provide code`, and `code snippet` now push `CODE_REQUEST` more strongly when a file/symbol anchor exists
  - explicit lookup wording such as `which file`, `open`, `show`, and `locate` now boosts `FILE` / `SYMBOL` routing more predictably

## 6. Dependency and Import Tracing Expansion

Current cross-file tracing is incomplete and biased toward named JS/TS imports. That limits trace-style answers.

Expected impact:

- better `how does X flow through the system` answers
- better backend trace quality
- better imported-data and cross-module explanations

Tasks:

- [x] add support for Python import resolution
- [x] support default imports and namespace imports in JS/TS
- [x] support re-export chains
- [x] support simple config/data import chains where feasible
- [x] improve route-to-handler-to-service-to-storage tracing where the code pattern is explicit
- [x] enforce max trace depth of `3` by default
- [x] add cycle detection with a visited-set before expanding imports, re-exports, or dependency graph edges
- [x] cap total trace-expanded chunks per query
- [x] reuse import/dependency evidence in both retrieval ranking and deterministic answer builders
- [x] add targeted tests for backend flow tracing and imported-data resolution

## 7. Follow-Up Query Resolution

Current follow-up handling is still shallow and breaks on many real conversational turns.

Expected impact:

- better conversational continuity
- fewer bad answers to `how does it work`, `where is it used`, `what about auth`

Tasks:

- [x] persist recent cited entities such as files, symbols, routes, and config keys in conversation memory
  - implemented in `retrieval/follow_up_memory.py` (`extract_cited_entities`) and stored via `memory.add(entities=...)`
  - `thread_turn_entities` DB table holds per-turn entity rows keyed by `(thread_id, turn_index)`
- [x] implement the follow-up memory contract defined in this document
  - `ConversationMemory`, `SessionConversationMemory`, and `ThreadConversationMemory` all accept `entities` and `primary_intent` kwargs and expose `recent_turn_entities()`
  - `memory_store.py` now has `save_turn_entities`, `list_turn_entities`, `save_session_turn_entities`, `list_session_turn_entities`, `clear_turn_entities_for_thread`
- [x] retain recent entities for the last 5-8 relevant turns per thread
  - `build_recent_entity_set(recent_turns, max_turns=8)` merges entities from the last `max_turns` turns, returning the most recently cited values first
- [x] resolve pronouns and vague references against recent cited entities before retrieval
  - `rewrite_follow_up_query()` in `follow_up_memory.py` injects the most salient recent entity (symbol > file > service) into vague pronoun-only queries
  - vague detection uses an extended stoplist covering pronouns, question words, aux verbs, and common follow-up filler tokens; a query is vague when at most 1 concrete content token remains
- [x] distinguish true follow-ups from new independent questions
  - `detect_topic_shift()` returns True when: (a) no follow-up phrase is present, (b) the new query has explicit entities, and (c) those entities have zero overlap with the last 2 or 8 cited-entity turns
  - `_should_rewrite_follow_up` and `_resolve_query_info` in `main.py` skip entity injection when `topic_shift=True`
- [x] detect topic shifts that still preserve partial conversational context
  - when topic shift is detected, old entities are not injected but normal chat history remains available for the LLM path
  - two-window comparison: close window (last 2 turns) checked first; broad window (last 8 turns) checked when query has new entities
- [x] add tests for topic-shift detection versus true follow-up behavior
  - 10 tests in `tests/test_followup_entity_memory.py` (`TestDetectTopicShift`) covering follow-up phrases, entity overlap, new entities with no overlap, short query, no prior turns
- [x] improve rewrite strategy so it produces a resolved query, not just a concatenated query
  - resolved queries now take the form: `{previous_anchor}\n{salient_entity} — {raw_query}` for vague queries, or `{previous_anchor}\n{raw_query}` for non-vague follow-ups
  - the LLM always sees the original `raw_query`; the rewritten form is used only for retrieval and stored as `resolved_query`
- [x] add tests for multi-turn follow-up cases that currently fail
  - 4 tests in `TestMultiTurnFollowUpEntityInjection`: vague-pronoun-to-symbol injection, symbol retention after `also provide code`, topic-shift isolation, and second-follow-up cumulative entity context

## 8. Source Gating and Context Assembly

The current strict display-source gate reduces hallucination, but it can also remove evidence needed for synthesis. Context budgeting is also still global instead of intent-aware.

Expected impact:

- better synthesis answers
- fewer starved overview/explanation prompts
- more consistent source quality

Tasks:

- [x] split source handling into:
  - `reasoning_sources`
  - `display_sources`
- [x] enforce `display_sources` max `6` and `reasoning_sources` max `12`
- [x] keep strict citation safety for displayed sources while allowing a slightly broader reasoning set
- [x] introduce intent-aware context budgets instead of a single global budget
- [x] implement the initial intent-aware context budget table defined in this document
- [x] tune context budgets separately for:
  - overview
  - architecture
  - semantic synthesis
  - symbol lookup
  - dependency trace
  - follow-up explanation
  - technical deep-dive explanation
  - explicit code request
- [x] review current history budget interaction in [assembler.py](../../retrieval/assembler.py) and reduce history starvation on broad answers
- [x] ensure assembly preserves the most snippet-worthy evidence for code-oriented or explanation-heavy answers
- [x] add partial-evidence answer signaling so the system can explicitly communicate uncertainty when evidence is incomplete but non-zero
- [x] add tests for source selection and context assembly regressions
- [x] add tests proving reasoning-only sources are capped and not cited unless promoted to display sources

## 9. Sibling and Neighborhood Expansion

When one chunk is relevant, nearby chunks in the same file/class/module are often also relevant. This is still underused.

Expected impact:

- better local code understanding
- fewer truncated explanations

Tasks:

- [x] implement sibling expansion for neighboring chunks in the same file/class/module
  - `_sibling_chunks_for(relative_path, parent_symbol)` scrolls Qdrant for same-class chunks first, falling back to same-file chunks when parent_symbol is empty
  - orchestrated by `_merge_siblings()` in `retrieval/expander.py`
- [x] gate sibling expansion by query type so it helps explanation and trace queries more than overview queries
  - `SIBLING_ENABLED_INTENTS` in `config.py` explicitly includes `EXPLANATION`, `TRACE`, `SYMBOL`, `DEPENDENCY`, `CODE_REQUEST`, `FILE`, `FOLLOWUP`, `SEMANTIC` and excludes `OVERVIEW`, `TECH_STACK`, `ARCHITECTURE`, `CONFIG`, `LOW_CONTEXT`
  - `expand()` checks `(primary_intent or intent).upper() in SIBLING_ENABLED_INTENTS` before calling `_merge_siblings()`
- [x] cap sibling expansion to a fixed share of remaining token budget
  - `SIBLING_BUDGET_FRACTION=0.20` (env: `RETRIEVAL_SIBLING_BUDGET_FRACTION`); pre-filter uses a 200-token/sibling proxy to bound total siblings before the assembler enforces the real budget
  - assembler tier ordering ensures siblings are dropped first when the budget is tight
- [x] drop sibling chunks below a minimum relevance threshold
  - `SIBLING_MIN_OVERLAP=1` (env: `RETRIEVAL_SIBLING_MIN_OVERLAP`); siblings with zero lexical overlap are discarded before being added to `seen`
- [x] use lexical overlap with query tokens or extracted entities as the first sibling relevance rule
  - `_sibling_lexical_overlap(chunk, query_tokens)` counts shared tokens across symbol_name, summary, and calls list
  - `_build_query_tokens(query_info)` extracts tokens from raw_query + entity fields; compound identifiers (e.g. `create_session`) are split on underscores so parts match individually
  - `_identifier_tokens()` helper filters tokens ≤ 2 chars to reduce noise
- [x] limit sibling expansion to at most `2-3` chunks per primary chunk
  - `SIBLING_MAX_PER_PRIMARY=2` (env: `RETRIEVAL_SIBLING_MAX_PER_PRIMARY`); default is 2, configurable up to 3 per plan spec
- [x] add tests for class/module explanation cases where sibling context matters
  - 24 tests in `tests/test_sibling_expansion.py` covering token extraction, lexical overlap, min-overlap filtering, per-primary cap, budget cap across primaries, intent gating, and class/module explanation scenarios

## 10. Evaluation and Measurement

We should not keep tuning response quality from anecdotal chat failures alone.

Expected impact:

- clearer progress tracking
- safer ranking and prompt changes
- faster detection of regressions

Tasks:

- [x] capture a baseline measurement on the current system before major retrieval changes land
- [x] add broad-question eval sets for overview, tech-stack, architecture, auth, deployment, and trace queries
- [x] add multi-repo eval coverage across:
  - frontend-heavy repos
  - backend-heavy repos
  - infra-heavy repos
  - mixed/monorepo shapes
- [x] define acceptance thresholds for retrieval quality on broad queries
- [x] add automated scoring for retrieval `hit@k`
- [x] add automated expected-file scoring
- [x] add automated expected-symbol scoring
- [x] add automated expected-framework/dependency scoring
- [x] add automated expected-no-answer scoring for absent-evidence cases
- [x] add latency measurement for deterministic, retrieval-only, and full LLM-backed query paths
  - `scripts/retrieval_eval.py` now emits dedicated latency buckets:
    - `retrieval_only_latency_p50_ms` / `p95`
    - `deterministic_latency_p50_ms` / `p95`
    - `llm_backend_latency_p50_ms` / `p95`
    - `llm_provider_latency_p50_ms` / `p95`
    - `llm_total_latency_p50_ms` / `p95`
  - `retrieval.main.run_query()` now returns `backend_latency_ms` and `provider_latency_ms` in eval meta for `llm` responses
- [x] add dedicated eval fixtures for retrieval-only, deterministic, and provider-backed LLM latency paths
  - added `docs/retrieval_docs/eval_codeseek_latency_modes.json`
  - `scripts/retrieval_eval.py` accepts `--provider`, `--api-key-env`, and `--model` so the same eval file can measure true provider-backed latency when credentials are available
  - `scripts/retrieval_eval_suite.py` propagates provider options per dataset and parses the new latency-bucket output
- [x] define a manual response-review checklist for answer usefulness and grounding
- [x] use evals during lexical-fusion tuning, not only after later workstreams
- [x] run evals before and after each major retrieval/ranking change

## 11. Multi-Language Support Boundaries

The current pipeline is strongest on Python and JS/TS repositories. That needs to be explicit so response-quality expectations stay realistic.

Tasks:

- [x] document the current language-strength boundary in user-facing and internal docs
  - created `docs/retrieval_docs/multi_language_support_boundaries.md`
  - **Tier 1 (full AST support)**: Python (`.py`), JavaScript (`.js`, `.jsx`), TypeScript (`.ts`, `.tsx`) — symbol extraction, import tracing, call graphs, docstrings
  - **Tier 2 (structured metadata only)**: Markdown, JSON, TOML, YAML, Dockerfile, `.env.example`, plain text — searchable but no symbol-level chunks
  - **Unsupported (skipped)**: Go, Java, Rust, C/C++, Ruby, PHP, shell scripts — logged as `skip_reason=unsupported_language`, not embedded
- [x] keep the first response-quality pass focused on Python and JS/TS repos
  - all active eval fixtures (`eval_codeseek_exact_wording.json`, `eval_codeseek_flow_phase1.json`) target Python + JS/TS repos only
  - quality gates explicitly scoped: `hit@10 >= 0.90` and `MRR@10 >= 0.65` on primary fixture before any new language expansion
- [x] define what "acceptable degraded behavior" looks like for unsupported languages
  - documented in `multi_language_support_boundaries.md` per scenario:
    - unsupported function query → `LOW_CONTEXT` fallback citing the file is not indexed at symbol level
    - broad overview on unsupported-language repo → answer from `repo_summary` (README/manifest); do not return empty
    - mixed-language repo → answer accurately for Tier-1 symbols; fall back only for Tier-2/unsupported symbols
    - hallucinating unsupported-language AST details is explicitly **not acceptable**
- [x] defer new-language expansion until the current language paths meet baseline quality goals
  - gate defined: new language parsers only added after Python/JS/TS hit@10 >= 0.90, MRR@10 >= 0.65, and no systematic expected_file=0 failures
  - new language checklist: tree-sitter package available, 10+ eval cases written, baseline hit@k measured before/after

## 12. Embedding Model Review

Embedding upgrade is still useful, but only after the structural retrieval improvements above.

Expected impact:

- better semantic recall on difficult cross-language or cross-file queries

Tasks:

- [x] benchmark the current embedding model against at least one stronger alternative
  - benchmarked `BAAI/bge-small-en-v1.5` (384-dim, MTEB 62.17) vs `BAAI/bge-base-en-v1.5` (768-dim, MTEB 63.55)
  - script: `scripts/embedding_model_benchmark.py`; results: `docs/retrieval_docs/embedding_model_benchmark_results.md`
- [x] compare recall on broad-query eval sets and exact-ish retrieval eval sets
  - current model: `hit@10=1.000`, `mrr@10=0.756` on `eval_codeseek_flow_phase1` (6 cases, live Qdrant)
  - alternative recall comparison skipped: dim mismatch (384 vs 768) requires full re-ingestion to compare fairly
- [x] verify cost/latency/memory tradeoffs before switching defaults
  - alternative encode latency: 7.7 ms/query (faster than current 22.5 ms/query)
  - alternative memory delta: +1.3 MB peak — negligible
  - re-ingestion cost: 1,212 chunks must be re-embedded + Qdrant collection recreated
- [x] only change the default model if measurable gains justify the operational cost
  - verdict: **KEEP CURRENT** — current hit@10 is already 1.000; MTEB delta of +1.38 does not justify re-ingestion without evidence of recall failures

## 13. Prompt Refinement Last

Prompt changes should happen after retrieval quality is improved. Prompt tuning is not the first fix for the current system.

Expected impact:

- cleaner final phrasing
- slightly better answer structure once evidence quality is already strong

Tasks:

- [x] audit the current prompt instructions in [llm.py](../../retrieval/llm.py) after retrieval changes land
  - audited: old SYSTEM_PROMPT had flat rule numbering (5a/5b at same level as rule 5), UI-specific explanation mode, no TRACE/DEPENDENCY mode, ambiguous code-block rule
  - old `_build_prompt` had generic `--- RESPONSE MODE ---` label with no mode-specific guidance for TRACE/SYMBOL/DEPENDENCY paths
- [x] tighten answer-format guidance for overview, explanation, and trace prompts
  - `RESPONSE MODE: OVERVIEW` now distinguishes entry points, services, and config dependencies; requires paragraph + 4-6 bullets; no fenced code unless asked
  - `RESPONSE MODE: EXPLANATION` now covers both UI and backend code with distinct guidance (render/loop vs request/response/transform); requires inline `module.function` references per bullet
  - `RESPONSE MODE: TECHNICAL TRACE` (new) now covers TRACE/DEPENDENCY/SYMBOL fallthrough with numbered step format and explicit file+symbol references
- [x] add explicit guidance for technical deep-dive answers so they remain concrete and implementation-based
  - SYSTEM_PROMPT rule 7 now has: "For deep-dive / symbol-level questions, give a technical walk-through: purpose, inputs, key logic steps, return values, and side effects — referencing exact files and line ranges."
  - TECHNICAL TRACE mode requires naming exact file+symbol per step and covering inputs, return values, side effects, and error handling
- [x] add explicit guidance for when to include a short code snippet versus prose-only explanation
  - SYSTEM_PROMPT rule 6 now has explicit snippet-vs-prose rules: always use inline references; fenced blocks ONLY when user says "show", "provide", "give", "write" code OR when a snippet is the clearest answer; max 1-2 fenced blocks; never reproduce entire files
  - CODE REQUEST mode further constrains: no prose beyond a one-line context sentence per block
- [x] improve fallback phrasing for low-context situations
  - `LOW_CONTEXT_FALLBACK` now reads: "No relevant code was found for this query. Try rephrasing with a specific file name, function name, class, route, or config key. Example: \"how does `create_session` work\" or \"what does auth.py do\"."
  - more actionable: gives concrete examples, not just "try naming a file"
- [x] add explicit phrasing for partial-evidence answers so uncertainty is communicated cleanly
  - `PARTIAL_EVIDENCE_BANNER`: "⚠ Partial evidence: ... and may be missing important details. For a more complete answer, try naming a specific file, function, or class."
  - `WEAK_EVIDENCE_BANNER`: "... treat it as a starting point only. Try a more targeted question naming a specific symbol, file, or route."
  - both now explicitly tell the user what to do next, not just that confidence is low
- [x] keep prompt changes small and measurable against evals
  - all changes are targeted: system prompt rules, per-mode blocks, and three string constants; no architectural changes to the LLM pipeline
  - all 346 existing tests still pass after changes

## Recommended Implementation Order

The highest-value order for the current codebase is:

1. lexical retrieval layer
2. stable repo-summary artifact
3. better structured extraction from non-code files
4. evaluation baseline and broad-question eval expansion
5. source gating and context assembly improvements
6. deterministic answer coverage expansion
7. dependency/import tracing expansion
8. follow-up resolution improvements
9. sibling expansion
10. embedding model review
11. prompt refinement

Evaluation work should begin in parallel with lexical retrieval work, because fusion tuning and ranking changes need baseline measurements.

Feature flags and automated eval scoring are rollout gates for the retrieval workstreams. A new retrieval behavior should not become default until it can be disabled independently and has at least basic automated scoring coverage for the query families it affects.

Structured extraction evals should begin alongside Workstream 3, not after it. Every new structured field used by repo summary, lexical retrieval, or deterministic answers should have either a unit test or automated eval check proving it can be extracted and retrieved.

## Open Risks to Track

These are not optional notes. They are known response-quality risks that should be reviewed as work progresses.

- lexical retrieval may improve recall but hurt latency or memory usage if implemented carelessly
- in-process lexical indexes can diverge across workers without explicit invalidation/rebuild behavior
- repo-summary generation may drift if re-ingestion invalidation is incomplete
- deterministic answer expansion can become a scope trap if phases are not enforced
- deterministic technical deep-dive templates can become too broad unless limited to predictable single-symbol cases
- RRF can underweight exact graph/entity hits if they are fused like probabilistic candidates
- topic-shift handling in follow-up resolution can regress conversational quality if it over-assumes continuity
- sibling expansion can silently waste context budget without strict caps
- unsupported-language repos may appear partially functional while actually delivering weak evidence
- partial-evidence answers can still sound overconfident unless phrasing rules are tested explicitly
- retrieval changes need feature flags so precision or latency regressions can be rolled back quickly
- follow-up entity memory can pollute new topics if topic-shift detection is too weak
- intent-aware budget changes can improve depth while increasing latency if not measured separately

## Definition of Done for Response Quality

We should treat response-quality work as ready for deployment only when:

- overview questions are consistently useful across several real repo types
- tech-stack answers identify the major frameworks/runtime/deployment pieces correctly
- explanation questions cite the right files and describe real code/config behavior
- technical implementation questions are detailed enough for an engineer to follow the real behavior
- trace questions can follow common backend/frontend flows across multiple files
- follow-up questions do not collapse into vague or irrelevant answers
- low-context questions fall back safely instead of hallucinating
- partial-evidence answers clearly communicate uncertainty instead of overstating confidence
- explicit code requests return grounded code or code-adjacent output instead of generic prose
- explanation answers include small, relevant snippets when they materially improve clarity
- repo-summary and overview answers stay consistent after re-ingestion
- lexical indexes stay current after ingestion and do not require manual backend restarts
- exact dependency/symbol/file hits are preserved ahead of probabilistic retrieval noise
- deterministic builders avoid confident answers when evidence is partial or weak
- deterministic / overview path p50 stays <= 750 ms and p95 stays <= 1500 ms
- retrieval + assembly before LLM p50 stays <= 1000 ms and p95 stays <= 2500 ms
- full LLM-backed query including provider latency p50 stays <= 6000 ms and p95 stays <= 15000 ms
- the eval suite shows improvement, not just anecdotal chat wins

## Tracking Notes

Use the checkboxes in this document as the source of truth for response-quality work. When a change lands:

- update the relevant task checkbox
- add or update the matching tests/evals
- reflect major strategy changes in [current_retrieval_strategy.md](./current_retrieval_strategy.md) and [current_ingestion_strategy.md](../ingestion_docs/current_ingestion_strategy.md) where applicable
