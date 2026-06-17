# Overview Prompt Tracker

Last updated: 2026-06-05

## Purpose

This document tracks the current state of broad repository-understanding prompts in CodeSeek, with emphasis on:

- overview prompts
- architecture prompts
- structure/module prompts
- runtime-flow prompts
- source/context quality before prompt/LLM refinement

The current focus is retrieval/context quality first, not prompt tuning.

## Current Goal

Improve answers for broad repo-understanding questions by fixing:

- source contamination
- missing architecture anchors
- inconsistent repo summary usage
- prompt-shape sensitivity for short overview prompts

## Current High-Level Status

The system is in a mixed state:

- broad overview handling has improved compared to the initial state
- self-referential retrieval helper sources have been removed from overview answers
- noisy files such as fixture repos and ingestion state files have been filtered for overview paths
- architecture/module prompts now classify more reliably as `ARCHITECTURE` locally
- short overview prompts now prepend `repo_summary` and backend architecture anchors more aggressively before display/source capping
- monorepo-prefixed chunk paths are now resolved against subdirectory repo roots during assembly and deterministic source reads
- assembly now falls back to stored chunk payload text when the session repo workspace is missing on disk
- short overview prompts now reach the answer path end-to-end instead of failing with `assembled_sources=0`
- repo-level prompts are now receiving `repo_summary` context consistently enough to answer
- architecture prompts now prepend stronger backend/runtime/configuration anchors in display-source selection
- architecture/overview assembly now prioritizes concise backend/runtime/ingestion/config anchors ahead of large README-style blocks
- architecture answers now build a bucket-based source set from broader retrieved chunks instead of relying only on README-dominant shown sources
- architecture search now explicitly injects exact structural file hits for key file hints such as `backend/retrieval/api_service.py`, `backend/retrieval/main.py`, and `backend/rag_ingestion/main.py`
- live debugging confirmed one earlier architecture failure was caused by a bad session collection with only `42` points and almost no code chunks; a clean re-index rebuilt the same session collection to `1398` embeddings across normal code/config/doc paths
- after re-index, architecture prompts now surface real code architecture context again, for example `repo_summary` plus `backend/retrieval/main.py`
- architecture selection now fills missing API/ingestion/config buckets from deterministic local repo anchors when the retrieved pool is still incomplete but the repo workspace exists on disk
- architecture search now prefers representative indexed symbols such as `_query_impl`, `run_query`, and `run_pipeline` over incidental same-file symbols when injecting exact structural file hits
- architecture selection now prefers indexed architecture chunks over same-path local fallback file summaries and avoids duplicate same-path source cards
- architecture file injection is now triggered by architecture-shaped query wording even when scored intent lands on `OVERVIEW`, reducing fallback-heavy results for prompts like `Give me a high-level architecture overview of this codebase.`
- exact architecture file-hit scans now inspect a wider set of same-path chunks, reducing cases where earlier incidental symbols beat the intended representative architecture symbol
- architecture file injection now actively promotes the best same-path indexed architecture chunk even when a weaker chunk from that file already exists lower in the merged candidate pool
- architecture selection now attempts exact indexed bucket fallback from Qdrant before using deterministic local file fallback, so missing API/ingestion/config buckets can still resolve to real indexed chunks when search does not surface them directly
- architecture/structure prompts still surface too little backend/runtime/configuration context

Current conclusion:

- the main blocker is no longer total context absence for broad prompts
- the remaining problem is context completeness and source mix for architecture/structure queries
- it is not primarily an LLM-model problem
- prompting/rendering work should come after context is consistently correct

## Prompt Categories And Current Behavior

### 1. Simple Overview Prompts

Examples:

- `What is this project about?`
- `Give me a repository overview.`

Current behavior:

- now returns repo-level context instead of the low-context fallback
- now commonly cites `repo_summary`
- can still be noisy or overly metadata-heavy
- repo summary is now consistently surfaced enough for basic repo-overview coverage

Current issue type:

- overview evidence quality / summarization noise
- backend anchor coverage is still thinner than desired

### 2. Architecture Prompts

Examples:

- `Give me a high-level architecture overview of this codebase.`
- `How is this project structured?`
- `How is this codebase structured?`

Current behavior:

- improved from earlier bad states
- reaches deterministic architecture mode instead of the generic low-context fallback
- now validated end-to-end against a repaired live collection
- now includes the required bucket mix for tested prompts:
  - `repo_summary`
  - `backend/retrieval/api_service.py`
  - `backend/retrieval/main.py`
  - `backend/rag_ingestion/main.py`
  - `backend/docker-compose.yml`
- still often falls back to generic lines such as:
  - `Runtime/service structure is only partially visible in retrieved evidence.`
  - `Module boundaries are only partially visible in retrieved evidence.`
- still sometimes uses deterministic local fallback file anchors in the final source set instead of only indexed retrieved chunks

Current issue type:

- architecture evidence is still too thin in the shown source set
- repo summary is present, but backend architecture anchors are not winning often enough

### 3. Module / Subsystem Prompts

Examples:

- `What are the main modules and what does each one do?`
- `What are the core modules in this codebase?`
- `Describe the top-level subsystems in this repository.`

Current behavior:

- significantly better than the earliest state
- no longer dominated by retrieval helper internals in many cases
- still sensitive to exact prompt shape
- may still produce incomplete subsystem breakdowns if architecture evidence is too thin

Current issue type:

- architecture/module evidence still inconsistent
- renderer quality is secondary, but not yet the first bottleneck

### 4. Runtime Flow Prompts

Examples:

- `What is the runtime flow of this system from startup to handling a user query?`
- `How does a request move through the backend?`
- `Trace a query from the API endpoint to final answer generation.`

Current behavior:

- these generally perform better than broad overview questions
- the retrieval path already has stronger anchors for flow/orchestration prompts
- still may include some source noise or partial phrasing issues, but not the main blocker right now

Current issue type:

- lower priority than overview/architecture prompts

## Earlier Failure States

These were observed before the current fixes:

- overview prompts citing retrieval helper functions such as:
  - `_is_overview_query`
  - `query_is_overview_summary`
  - `build_overview_answer`
  - `_architecture_module_points`
- module prompts answering with internal retrieval implementation details instead of repo modules
- overview/module prompts picking noisy files such as:
  - `.rag_ingestion_state.json`
  - fixture repo files under `backend/tests/fixtures/...`
  - retrieval planning/docs files under `backend/docs/retrieval_docs/...`

These specific failures have been reduced or removed in the current state.

## Fixes Implemented So Far

### Retrieval / Query Understanding

- expanded architecture/overview phrase coverage in query understanding
- added architecture file injection for structure/module prompts
- fixed intent scoring so architecture-triggered file injection does not incorrectly force short structure prompts into `FILE`
- short structure prompts now classify locally as `ARCHITECTURE`

Relevant areas:

- `backend/retrieval/query_processor.py`

### Search / Overview Candidate Injection

- improved overview candidate prioritization toward backend architecture anchors
- added overview candidate exclusions for noisy files
- expanded overview fast-path query detection for more structure/module prompts
- architecture prompts now prepend exact structural file hits from `entities.files` when those files exist in Qdrant, instead of relying only on dense README-style retrieval
- exact structural file hit injection now prefers representative indexed architecture symbols such as `_query_impl`, `run_query`, and `run_pipeline` over incidental same-file symbols
- architecture file injection is now activated for architecture-shaped wording as well as explicit `ARCHITECTURE` primary intent, so architecture-overview prompts do not depend as heavily on local bucket fallback

Relevant areas:

- `backend/retrieval/searcher.py`

### Source Filtering

- suppressed self-referential overview helper sources from overview/module responses
- applied filtering to both display and reasoning source paths
- added display-time prepending of `repo_summary`, `backend/README.md`, and backend runtime anchors for broad overview/module prompts so short prompts retain richer evidence after source capping
- added architecture-specific prepending of backend/runtime/configuration anchors such as `backend/retrieval/api_service.py`, `backend/rag_ingestion/main.py`, `backend/docker-compose.yml`, and `backend/.env.example`

Relevant areas:

- `backend/retrieval/source_filter.py`

### Overview Answer Source Preference

- reduced plain `README.md` dominance inside deterministic overview answer source selection
- backend architecture anchors now outrank plain `README.md` when both are already present in the shown source set
- backend-local sessions can now still read `backend/README.md` and `backend/...` chunk paths even when the active repo root is already the `backend` subdirectory

Relevant areas:

- `backend/retrieval/code_answers.py`

### Architecture Source Coverage

- architecture prompts no longer rely only on README-heavy shown sources when broader retrieved chunks contain better structural anchors
- architecture source selection now enforces category coverage when available:
  - repo/docs
  - API surface
  - orchestration
  - ingestion
  - config/deployment
- when one of those buckets is still missing from retrieved chunks, architecture selection now falls back to deterministic local repo anchors if the session workspace exists on disk
- when both indexed chunks and local fallback anchors exist for the same architecture path, indexed chunks now win and same-path duplicates are suppressed
- `run_query()` now returns that architecture-selected source set so UI source cards match the deterministic architecture answer evidence

Relevant areas:

- `backend/retrieval/code_answers.py`
- `backend/retrieval/main.py`

### Assembly / Repo-Root Path Resolution

- assembly now resolves stored chunk paths by safe suffix fallback when the active repo root is a subdirectory of the original indexed repo root
- this fixes the `0 tok` / low-context fallback case where source paths like `backend/retrieval/main.py` were being read relative to `/.../backend`, producing `/.../backend/backend/...`
- assembly also falls back to `content_excerpt` / summary payload text when the repo workspace no longer exists locally, so valid Qdrant hits do not collapse to zero assembled sources
- assembly now also prefers concise backend/runtime/ingestion/config anchors for architecture/overview intents, reducing the chance that large `README.md` files consume the context budget before structural anchors survive into `assembled_sources`

Relevant areas:

- `backend/retrieval/assembler.py`

## Current Evidence About What Works

The improved path is now confirmed to work end-to-end for broad prompts in the sense that context reaches the answer path.

Observed pattern:

- short overview prompts now surface:
  - `repo_summary`
  - at least one structural code anchor such as `backend/retrieval/main.py`
- the previous `assembled_sources=0` / low-context failure has been removed for these prompt shapes
- after repairing a bad live collection by re-indexing the active session repo, the architecture query `How is this codebase structured?` returned:
  - `repo_summary`
  - `backend/retrieval/main.py`
  - `README.md`
  - `deploy/.env.example`
- after the next architecture coverage pass, live prompts now return the full intended bucket mix, including:
  - `backend/retrieval/api_service.py`
  - `backend/retrieval/main.py`
  - `backend/rag_ingestion/main.py`
  - `backend/docker-compose.yml`
  - `repo_summary`

This means:

- the retrieval path is now capable of delivering broad repo context for short direct prompts
- the current remaining issue is no longer missing architecture buckets
- the remaining refinement area is reducing reliance on deterministic local fallback when indexed retrieved chunks already exist

## Current Evidence About What Still Fails

Short direct prompts still show these problems:

- repo summary content can be too raw / metadata-heavy
- some architecture prompts still surface local fallback file anchors in the final source list even when indexed retrieved chunks exist for the same paths
- architecture answer phrasing still defaults to generic text in some sections when richer indexed runtime evidence is available

Examples still weak:

- `What is this project about?`
- `Give me a high-level architecture overview of this codebase.`
- `How is this project structured?`
- `Give me a repository overview.`

## Root Cause Assessment

Current assessment:

1. This is not mainly an LLM capability issue.
2. Switching to `gpt-5.4-mini` did not materially improve the weak short-prompt behavior.
3. One major recent failure mode was bad session state: the live architecture session had an incomplete collection that omitted the expected Python code files entirely.
4. After re-index, the stronger architecture path now exists end-to-end and is returning real code anchors again.
5. The main remaining bottleneck is now retrieval-source quality within already-correct architecture buckets, especially preferring indexed chunk evidence over deterministic local fallback where possible.

## Current Prompt-State Summary

### Good / Improved

- overview responses no longer commonly cite retrieval helper internals
- fixture/state/retrieval-doc noise has been reduced from overview paths
- architecture classification for structure/module prompts is improved locally
- short overview prompts now reach the answer path with repo-level context
- `repo_summary` is now being surfaced for broad overview prompts
- repaired live collections now restore code-path retrieval for architecture prompts
- short architecture prompts can now surface `backend/retrieval/main.py` again after re-index
- live architecture prompts now surface API, orchestration, ingestion, and config/deployment buckets together
- architecture selector now suppresses same-path fallback duplicates and prefers representative indexed symbols

### Still Weak

- cleanliness of repo-summary usage
- reducing `local fallback` dependence when indexed architecture chunks already exist
- runtime/deployment phrasing depth after the right buckets are present

## Next Retrieval-Side Work

Do next:

1. Reduce `local fallback` use in architecture answers when indexed chunks for the same paths already exist in the retrieved pool.
2. Prefer representative indexed architecture chunks consistently for API/runtime/ingestion, not incidental classes or whole-file local summaries.
3. Re-test the architecture prompt subset live and check whether source cards keep the same bucket coverage but shift away from `local fallback`.
4. Continue fixing retrieval/context before changing prompt templates or answer prompting.

Update after latest implementation:

1. The earlier `repo_summary` surfacing and no-source failures are now fixed end-to-end for broad overview prompts.
2. The immediate next task was architecture/source completeness, and the first display-time anchor-prepending step for that is now implemented locally.
3. The next checkpoint is live re-testing of the bucket-based architecture path against the benchmark set.

## Benchmark Prompt Set

Use these prompts repeatedly after each change.

### Core Overview

- `What is this project about?`
- `Give me a high-level architecture overview of this codebase.`
- `How is this project structured?`
- `How is this codebase structured?`
- `Give me a repository overview.`

### Module Understanding

- `What are the main modules and what does each one do?`
- `What are the core modules in this codebase?`
- `Describe the top-level subsystems in this repository.`
- `Which parts of this project handle API requests, retrieval, and ingestion?`

### Runtime Shape

- `What is the runtime flow of this system from startup to handling a user query?`
- `How does a request move through the backend?`
- `Trace a query from the API endpoint to final answer generation.`

### Boundary / Deployment Understanding

- `What infrastructure and services does this project depend on?`
- `How are deployment and runtime configuration handled in this repo?`
- `What are the main environment and service boundaries in this codebase?`

### Tech Stack

- `What tech stack does this project use?`
- `Which frameworks, libraries, and infrastructure components are central here?`

## Working Rule For This Phase

Until overview sources are consistently correct:

- do not treat prompt tuning as the main fix
- do not blame the model first
- prioritize source quality, evidence breadth, and retrieval consistency
