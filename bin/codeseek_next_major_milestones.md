# CodeSeek Next Major Milestones Implementation Roadmap

## Purpose

This document defines the next major implementation milestones for CodeSeek after completing the RAGAS runtime, judge calibration, deterministic diagnostics, session binding guard, and evaluator comparison work.

The goal is to move from experimental evaluation tooling toward a stable product workflow where:

- users know whether a session is fresh or stale,
- evaluation results are trustworthy and actionable,
- wrong repo-root/session binding is visible and preventable,
- retrieval quality improvements are guided by deterministic evidence,
- RAGAS metrics are used safely instead of blindly,
- future CI and UI reporting can be built on top of stable primitives.

---

## 0. Current Completed Baseline

The following work is already complete and should be treated as the foundation.

### Completed RAGAS/eval infrastructure

1. **RAGAS runtime configurability**
   - `--ragas-timeout`
   - `--ragas-max-workers`
   - `--ragas-max-retries`
   - provider-specific runtime defaults
   - runtime info emitted in reports

2. **Metric isolation**
   - `--metrics answer_relevancy`
   - `--metrics context_precision`
   - `--metrics faithfulness`
   - invalid metric handling with structured `ERROR`

3. **RAGAS judge calibration analyzer**
   - joins RAGAS output, traces, and calibration query metadata
   - produces JSON and Markdown reports
   - identifies local judge/parser instability
   - identifies RAGAS/context mismatch cases

4. **Deterministic context-file diagnostics**
   - `expected_context_file_hit`
   - `expected_context_file_rank`
   - `expected_context_file_precision`
   - `expected_context_file_reciprocal_rank`
   - found/missing expected files
   - aggregate deterministic diagnostics

5. **Session binding guard**
   - `--expected-repo-root`
   - `--expected-collection`
   - fails before trace overwrite if wrong repo/session binding is detected

6. **Evaluator comparison workflow**
   - compares frozen traces across evaluator models/providers
   - supports metric isolation
   - supports subprocess timeout
   - supports verbose/heartbeat progress
   - produces JSON and Markdown comparison reports

### Current evaluation conclusion

The current evidence shows:

- deterministic expected-file retrieval can pass even when RAGAS `context_precision` is `0.0`,
- `context_precision` remains `0.0` across both smaller and larger local evaluators,
- `answer_relevancy` is a usable local smoke signal,
- `faithfulness` is slow and model-sensitive,
- local evaluator comparison should use frozen traces and small limits.

Therefore:

```text
Stable local smoke signals:
- deterministic expected-context-file diagnostics
- answer_relevancy

Diagnostic-only signals:
- context_precision
- faithfulness
```

Do not use RAGAS `context_precision` as a retrieval gate for current code-location traces.

---

## 1. Milestone: Evaluation Policy / Gating v1

### Goal

Define exactly which evaluation signals are allowed to fail a run, which are warnings, and which are diagnostic-only.

This prevents future confusion where a RAGAS metric reports a bad score even though deterministic retrieval is correct.

### Why this milestone matters

Right now CodeSeek has multiple evaluation signals:

- deterministic retrieval eval,
- deterministic conversation eval,
- RAGAS answer relevance,
- RAGAS context precision,
- RAGAS faithfulness,
- deterministic expected-file diagnostics,
- evaluator comparison output.

Without a policy, these signals can contradict each other and developers may tune the wrong system.

Example:

```text
Expected file retrieved at rank 1.
RAGAS context_precision = 0.0.
```

This should not trigger retrieval tuning. It should trigger a metric/reference-format investigation.

### Scope

Add a formal evaluation policy document and optionally a policy summary script.

### Proposed files

```text
docs/retrieval_docs/evaluation_policy.md
evals/eval_policy_summary.py
tests/test_eval_policy_summary.py
```

The script is optional for v1. The document is required.

### Policy categories

#### Hard gates

These can fail a run:

```text
- retrieval_eval.py status ERROR/FAIL
- conversation_eval.py status ERROR/FAIL
- expected_context_file_hit false for required code-location queries
- expected protected hits dropped
- exact-hit regression
- empty result rate above accepted threshold
```

#### Soft gates / warnings

These should warn but not fail by default:

```text
- answer_relevancy below local smoke baseline
- expected_context_file_rank worse than previous baseline
- answer_mentions_expected_terms false
- evaluator comparison shows model instability
```

#### Diagnostic-only

These should not fail local retrieval quality:

```text
- RAGAS context_precision for code-location traces
- RAGAS faithfulness on small local models
- evaluator-specific score disagreement
```

### Implementation details

#### `docs/retrieval_docs/evaluation_policy.md`

Include:

1. Current evaluation architecture.
2. Signal definitions.
3. Which signals are hard gates.
4. Which signals are soft warnings.
5. Which signals are diagnostic-only.
6. Why `context_precision` is not currently a code-location retrieval gate.
7. Recommended local smoke workflow.
8. Recommended CI workflow.
9. Future conditions under which `context_precision` may become usable.

#### Optional `evals/eval_policy_summary.py`

Inputs:

```bash
--retrieval-report ../evals/reports/latest.json
--conversation-report ../evals/reports/latest_conversation.json
--judge-calibration-report ../evals/reports/ragas_judge_calibration_latest.json
--ragas-report ../evals/reports/ragas_calibration_latest.json
--output-json ../evals/reports/eval_policy_summary.json
--output-md ../evals/reports/eval_policy_summary.md
```

Output:

```json
{
  "status": "PASS|WARN|ERROR",
  "hard_gate_status": "PASS",
  "warnings": [],
  "diagnostics": [],
  "recommendation": "..."
}
```

### Acceptance criteria

- Documentation clearly states that deterministic expected-file diagnostics are the primary retrieval-quality signal.
- Documentation clearly states that RAGAS `context_precision` is diagnostic-only for current code-location traces.
- Local smoke command is documented.
- CI-safe command set is documented.
- Developers know when retrieval tuning is justified.

### Validation commands

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_eval_policy_summary.py
```

If only docs are added:

```bash
git diff docs/retrieval_docs/evaluation_policy.md
```

### Commit message

```bash
git commit -m "Document evaluation policy for retrieval and RAGAS diagnostics"
```

---

## 2. Milestone: Repo / Session Freshness v1

### Goal

Show whether a CodeSeek session is indexed against the latest repository state.

Users must be able to answer:

```text
Is this session using the latest committed code?
Is the repository dirty?
What commit was indexed?
What commit is currently checked out?
Can I re-index to latest?
```

### Why this milestone matters

The recent RAGAS work exposed a deeper product issue:

```text
/home/arch/DEV/CodeSeek
vs
/home/arch/DEV/CodeSeek/backend
```

A session can be technically valid while pointing at the wrong repo root or stale code.

This milestone makes repo freshness visible and actionable.

### Current known DB fields

The session table already includes fields like:

```text
last_indexed_commit
current_commit_sha
current_branch
repo_dirty
repo_status_checked_at
files_indexed
chunks_generated
embeddings_stored
```

These should be used and improved rather than duplicating state.

### Backend scope

Required backend capabilities:

1. Compute current repo status:
   - current branch
   - current commit SHA
   - dirty worktree
   - untracked files if relevant
   - whether indexed commit equals current commit

2. Expose status in session detail API.

3. Add a refresh-status endpoint:
   - checks current repo status without re-indexing.

4. Add an index-latest endpoint/action:
   - re-index current repo state,
   - update embeddings,
   - remove stale embeddings for deleted files,
   - update indexed commit fields.

5. Prevent stale/incorrect state from being hidden.

### Proposed backend files

```text
backend/retrieval/repo_freshness.py
backend/retrieval/session_indexer.py
backend/retrieval/api_service.py
backend/retrieval/db.py
backend/tests/test_repo_freshness.py
backend/tests/test_session_indexer.py
backend/tests/test_api_service_sessions.py
```

### Proposed API fields

Session response should include:

```json
{
  "repo_status": {
    "status": "fresh|stale|dirty|unknown|missing",
    "repo_root": "/home/arch/DEV/CodeSeek",
    "indexed_commit_sha": "...",
    "current_commit_sha": "...",
    "current_branch": "monorepo-restructure",
    "dirty_worktree": false,
    "checked_at": "...",
    "indexed_at": "...",
    "files_indexed": 123,
    "chunks_generated": 456,
    "embeddings_stored": 456
  }
}
```

### Status rules

```text
fresh:
  indexed_commit_sha == current_commit_sha
  dirty_worktree == false

stale:
  indexed_commit_sha != current_commit_sha
  dirty_worktree == false

dirty:
  dirty_worktree == true

missing:
  repo_root does not exist

unknown:
  git status cannot be determined
```

### API endpoints

Suggested endpoints:

```text
GET  /api/v1/sessions/{session_id}
POST /api/v1/sessions/{session_id}/refresh-repo-status
POST /api/v1/sessions/{session_id}/index-latest
```

### Frontend scope

Show a session freshness badge in the session view/sidebar.

### Proposed frontend files

```text
frontend/src/components/SessionView.jsx
frontend/src/components/SessionItem.jsx
frontend/src/components/RepoStatusBadge.jsx
frontend/src/utils/api.js
```

### UI states

#### Fresh

```text
Indexed commit matches current commit.
Show green/fresh badge.
```

#### Stale

```text
Current commit differs from indexed commit.
Show yellow/stale badge.
Show "Index latest" button.
```

#### Dirty

```text
Working tree has uncommitted changes.
Show orange/dirty badge.
Explain that indexed commit may not include uncommitted files.
```

#### Missing

```text
Repo root no longer exists.
Show red/missing badge.
Disable index-latest.
```

### Acceptance criteria

- Session detail shows indexed commit and current commit.
- UI shows fresh/stale/dirty/missing state.
- User can refresh repo status.
- User can trigger index-latest.
- Re-index updates embeddings and stale/deleted-file state.
- Tests cover clean/stale/dirty/missing repos.

### Validation commands

```bash
PYTHONPATH=. .venv/bin/pytest   tests/test_repo_freshness.py   tests/test_session_indexer.py   tests/test_api_service_sessions.py
```

Frontend:

```bash
npm test
npm run build
```

### Commit message

```bash
git commit -m "Add repo freshness status and index-latest workflow"
```

---

## 3. Milestone: Session / Root Binding Visibility

### Goal

Make repo-root and collection binding visible in the product UI and safer in backend workflows.

### Problem

The system can accidentally query or evaluate:

```text
repo_root = /home/arch/DEV/CodeSeek/backend
collection = repository_chunks__local__backend
```

when the intended target is:

```text
repo_root = /home/arch/DEV/CodeSeek
collection = repository_chunks__local__codeseek
```

This causes false retrieval failures and confusing evaluation results.

### Scope

Expose and validate binding metadata.

### Backend work

Add or expose:

```json
{
  "repo_root": "...",
  "collection": "...",
  "repo_full_name": "...",
  "repo_url": "...",
  "session_id": "...",
  "binding_health": {
    "status": "ok|warning|mismatch",
    "reason": "..."
  }
}
```

### UI work

Display in session details:

```text
Repo root:
Collection:
Indexed commit:
Current commit:
Session ID:
```

Optionally hide behind an “Advanced session details” section.

### Warning cases

```text
- repo_root is a subdirectory of another known repo root
- collection name does not match expected collection naming rules
- repo_root path no longer exists
- session has zero indexed files
- session collection has zero vector results
```

### Proposed files

Backend:

```text
backend/retrieval/session_indexer.py
backend/retrieval/isolation.py
backend/retrieval/api_service.py
backend/tests/test_session_binding_visibility.py
```

Frontend:

```text
frontend/src/components/SessionView.jsx
frontend/src/components/SessionDebugPanel.jsx
frontend/src/components/SessionItem.jsx
frontend/src/utils/api.js
```

### Acceptance criteria

- UI clearly shows which repo root the session uses.
- UI clearly shows whether the session is full repo or subdirectory.
- User can identify wrong-root sessions without reading logs.
- Backend session response includes enough binding metadata for debugging.

### Commit message

```bash
git commit -m "Expose session binding metadata in API and UI"
```

---

## 4. Milestone: Retrieval Quality Improvements v1

### Goal

Improve retrieval ranking and answer evidence only after the evaluation policy and freshness/root-binding work are complete.

### Known current retrieval observations

From deterministic diagnostics:

```text
q004:
  expected file rank 1

q007:
  expected file rank 1

q008:
  expected file rank 3

overview/auth:
  retrieved evidence exists, but answer quality and coverage need improvement
```

### Important rule

Do not tune retrieval based only on RAGAS `context_precision`.

Tune retrieval only when deterministic diagnostics show:

```text
expected file missing
expected file rank degraded
protected hit dropped
conversation eval regressed
wrong top-1 increased
```

### Priority improvements

#### 4.1 Config/environment query improvements

Target query:

```text
Where is environment variable handling implemented?
```

Current expected file:

```text
backend/retrieval/config.py
```

Observed expected rank:

```text
rank 3
```

Goal:

```text
rank 1 or rank 2
```

Potential improvements:

- strengthen config/env intent detection,
- boost files with `file_type=config`,
- boost symbols/functions containing env/config terms,
- boost files named `config.py`,
- prevent parser/query-intent files from outranking config files for direct environment-variable queries.

Potential files:

```text
backend/retrieval/query_processor.py
backend/retrieval/query_intent.py
backend/retrieval/searcher.py
backend/retrieval/source_filter.py
tests/test_query_intent.py
tests/test_retrieval_eval.py
```

#### 4.2 Overview query improvements

Target query:

```text
What does this repo do?
```

Goal:

Return architecture/purpose-level files and summaries, not random helper functions.

Potential improvements:

- add overview-specific source selection,
- boost README/docs/package/config files,
- boost repo summary chunks,
- avoid narrow helper functions unless they represent core architecture.

#### 4.3 Auth flow improvements

Target query:

```text
How does auth work?
```

Goal:

Retrieve enough roles for complete lifecycle:

```text
auth entrypoint
session creation
session lookup
logout/session deletion
frontend callback
credential storage
```

Potential improvements:

- improve flow evidence role coverage,
- improve source expansion around auth symbols,
- boost domain:auth and auth lifecycle functions together.

### Acceptance criteria

- deterministic retrieval eval remains PASS,
- q008 expected file rank improves,
- no protected hit regressions,
- conversation eval remains PASS,
- overview/auth answers become more complete,
- no increase in wrong top-1 rate.

### Validation commands

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_retrieval_eval.py
```

Run:

```bash
CODESEEK_DB_BACKEND=sqlite CODESEEK_DB_PATH=/tmp/codeseek.sqlite3 .venv/bin/python evals/retrieval_eval.py   --session-id <session_id>   --golden ../evals/golden_queries.yaml   --output ../evals/reports/latest.json
```

Run:

```bash
CODESEEK_DB_BACKEND=sqlite CODESEEK_DB_PATH=/tmp/codeseek.sqlite3 .venv/bin/python evals/conversation_eval.py   --session-id <session_id>   --trees ../evals/conversation_trees.yaml   --output ../evals/reports/latest_conversation.json
```

### Commit message

```bash
git commit -m "Improve retrieval ranking for config, overview, and auth queries"
```

---

## 5. Milestone: Answer Quality / Response Mode Refinement

### Goal

Improve how CodeSeek answers once it has sources.

Retrieval quality and answer quality should be treated separately.

### Current response modes

Examples:

```text
source_location
overview_summary
flow_summary
low_context
```

### Problems to solve

#### Source-location answers

Should be concise and direct:

```text
Qdrant upsert happens in:
backend/rag_ingestion/stages/storage.py

Function:
store_chunks()

Call:
client.upsert(...)

Lines:
...
```

Avoid extra unrelated context.

#### Overview answers

Should explain:

```text
- what the repo does,
- backend purpose,
- frontend purpose,
- data/indexing flow,
- retrieval flow,
- major services,
- key entrypoints.
```

Avoid returning random function snippets as the main answer.

#### Flow answers

Should clearly state:

```text
complete evidence
partial evidence
missing evidence roles
```

Auth answers should include:

```text
- auth route entrypoint
- GitHub token exchange or callback
- session creation
- session lookup
- logout/session deletion
- frontend callback
```

### Proposed files

```text
backend/retrieval/code_answers.py
backend/retrieval/main.py
backend/retrieval/assembler.py
backend/tests/test_code_answers.py
backend/tests/test_answer_modes.py
```

### Acceptance criteria

- source-location answers are shorter and clearer,
- overview answers describe the repo instead of listing random helpers,
- flow answers list evidence roles,
- partial evidence is clearly labeled,
- tests cover response mode output shapes.

### Commit message

```bash
git commit -m "Refine deterministic answer modes for source, overview, and flow responses"
```

---

## 6. Milestone: Safe Eval Runner / CI Workflow

### Goal

Provide one command that runs the safe, stable evaluation suite.

### Problem

Currently evals are run manually and in many variants. This is powerful but error-prone.

### Proposed script

```text
evals/run_safe_evals.py
```

### Scope

The safe runner should run:

```text
1. retrieval_eval.py
2. conversation_eval.py
3. ragas_eval.py on frozen trace with answer_relevancy only
4. ragas_judge_calibration.py
5. optional evaluator comparison if explicitly enabled
```

### CLI

```bash
.venv/bin/python evals/run_safe_evals.py   --session-id <session_id>   --expected-repo-root /home/arch/DEV/CodeSeek   --expected-collection repository_chunks__local__codeseek   --output-dir ../evals/reports/safe_eval_latest
```

Optional flags:

```text
--include-ragas
--include-evaluator-compare
--include-faithfulness
```

### Output

```json
{
  "status": "PASS|WARN|ERROR",
  "retrieval_eval": {},
  "conversation_eval": {},
  "ragas_smoke": {},
  "judge_calibration": {},
  "warnings": [],
  "errors": [],
  "recommendation": "..."
}
```

### Acceptance criteria

- one command runs stable evals,
- wrong-root session fails early,
- output is easy to read,
- CI can call it,
- slow evaluator comparison is opt-in.

### Commit message

```bash
git commit -m "Add safe evaluation runner for retrieval and RAGAS smoke checks"
```

---

## 7. Milestone: Evaluation Reports UI

### Goal

Surface evaluation status in the CodeSeek frontend.

### Why this matters

The eval infrastructure is currently developer-only. Product users should see:

```text
Is this session healthy?
Is retrieval passing?
Is the repo stale?
Are RAGAS diagnostics trustworthy?
```

### UI concept

Add an “Evaluation” or “Diagnostics” panel in the session view.

### Display sections

#### Session health

```text
Repo root
Collection
Indexed commit
Current commit
Fresh/stale/dirty
```

#### Retrieval eval

```text
Status
File Hit@5
Symbol Hit@5
Label Hit@5
Wrong Top-1 Rate
Empty Result Rate
```

#### Conversation eval

```text
Status
Turns passed
File Hit@5
Symbol Hit@5
```

#### RAGAS smoke

```text
answer_relevancy
score health
null count
```

#### Deterministic context diagnostics

```text
expected file hit rate
mean rank
mean reciprocal rank
```

#### Warnings

```text
context_precision diagnostic-only
faithfulness slow/flaky locally
wrong repo binding detected
stale repo
```

### Proposed files

Backend:

```text
backend/retrieval/ragas_reports.py
backend/retrieval/api_service.py
backend/tests/test_ragas_reports.py
```

Frontend:

```text
frontend/src/components/EvaluationPanel.jsx
frontend/src/components/SessionView.jsx
frontend/src/utils/api.js
frontend/src/components/MetricBadge.jsx
```

### API endpoint

```text
GET /api/v1/sessions/{session_id}/evaluation-report
```

### Acceptance criteria

- frontend shows latest reports if present,
- missing reports are handled gracefully,
- warnings are clear,
- users can see stale/wrong-root status,
- no raw stack traces in UI.

### Commit message

```bash
git commit -m "Add evaluation diagnostics panel for sessions"
```

---

## 8. Milestone: Multi-provider / Evaluator Management

### Goal

Allow evaluator providers and models to be configured, compared, and reused.

### Scope

This is a later milestone. Do not prioritize before repo freshness and evaluation policy.

### Future features

```text
- evaluator presets
- local Ollama model picker
- OpenAI/OpenRouter/Gemini/Groq evaluator support
- evaluator comparison from UI
- saved evaluator comparison reports
```

### Backend files

```text
backend/retrieval/provider_store.py
backend/retrieval/llm.py
backend/evals/ragas_evaluator_compare.py
```

### Frontend files

```text
frontend/src/components/ApiTokensModal.jsx
frontend/src/components/EvaluatorSettings.jsx
frontend/src/components/EvaluationPanel.jsx
```

### Acceptance criteria

- user can select evaluator model,
- evaluator config is stored,
- comparison can be run with chosen presets,
- missing provider keys show clear setup instructions.

### Commit message

```bash
git commit -m "Add evaluator provider presets and comparison configuration"
```

---

## Recommended Implementation Order

### Phase 1: Lock evaluation interpretation

```text
1. Evaluation policy / gating v1
```

Reason:

```text
Before tuning anything, define what counts as failure.
```

### Phase 2: Fix product/session correctness

```text
2. Repo/session freshness
3. Session/root binding visibility
```

Reason:

```text
Users must know whether a session is indexed against the correct and latest code.
```

### Phase 3: Improve retrieval and answers

```text
4. Retrieval quality improvements
5. Answer quality / response mode refinement
```

Reason:

```text
Only tune retrieval after freshness/root correctness and eval policy exist.
```

### Phase 4: Operationalize evals

```text
6. Safe eval runner / CI workflow
7. Evaluation reports UI
```

Reason:

```text
Make evals repeatable and visible.
```

### Phase 5: Expand evaluator ecosystem

```text
8. Multi-provider / evaluator management
```

Reason:

```text
Only useful once local policies and reports are stable.
```

---

## Immediate Next Task Recommendation

The next implementation should be:

```text
Evaluation Policy / Gating v1
```

This is small, low-risk, and prevents wrong future decisions.

After that, implement:

```text
Repo/session freshness + index latest
```

That is the biggest user-facing product milestone.

---

## Immediate Agent Prompt: Evaluation Policy v1

Use this prompt when starting the next coding session:

```text
We have completed the RAGAS/eval foundation:
- runtime configurability
- metric isolation
- judge calibration analyzer
- deterministic context-file diagnostics
- session binding guard
- evaluator comparison workflow
- evaluator comparison timeout/heartbeat usability

Next task: implement Evaluation Policy / Gating v1.

Goal:
Define which evaluation signals are hard gates, soft warnings, and diagnostic-only. Do not tune retrieval or answer generation.

Only update:
- docs/retrieval_docs/evaluation_policy.md
- optionally evals/eval_policy_summary.py
- optionally tests/test_eval_policy_summary.py
- optionally docs/retrieval_docs/ragas_eval_usage.md

Policy:
Hard gates:
- retrieval_eval FAIL/ERROR
- conversation_eval FAIL/ERROR
- deterministic expected_context_file_hit false for required expected-file queries
- exact-hit regressions
- protected hit regressions
- empty result regressions above configured threshold

Soft warnings:
- answer_relevancy below local smoke baseline
- expected_context_file_rank worsens
- answer expected terms missing
- evaluator instability

Diagnostic-only:
- RAGAS context_precision for code-location traces
- RAGAS faithfulness on small local models
- evaluator disagreement

Required documentation:
- explain why context_precision is diagnostic-only right now
- explain that deterministic expected-file diagnostics are the retrieval-side signal
- explain local smoke recommendation:
  answer_relevancy + deterministic expected-file diagnostics
- explain faithfulness should run separately
- explain evaluator comparison is optional and slow

If implementing script:
evals/eval_policy_summary.py should read retrieval/conversation/RAGAS/judge-calibration reports and produce:
- status PASS/WARN/ERROR
- hard_gate_status
- warnings
- diagnostics
- recommendation

Validation:
PYTHONPATH=. .venv/bin/pytest tests/test_eval_policy_summary.py

Commit:
git commit -m "Add evaluation policy for retrieval and RAGAS diagnostics"
```
