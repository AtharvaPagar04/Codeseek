# CodeSeek RAGAS Validation Design

This document defines how RAGAS should be used in CodeSeek.

The primary goal is not to replace the existing retrieval eval suite. The primary goal is to produce a detailed validation record for each response so reviewers can see:

- the final answer that CodeSeek returned
- the exact context that answer was built from
- the retrieval and grounding scores for that response
- the file and symbol evidence behind the score
- the response mode and pipeline path that produced it
- enough metadata to debug why a score is high or low

This is a CodeSeek-specific design. It is grounded in the current pipeline shape:

- `retrieval/main.py`
- `retrieval/query_processor.py`
- `retrieval/searcher.py`
- `retrieval/expander.py`
- `retrieval/assembler.py`
- `retrieval/source_filter.py`
- `retrieval/code_answers.py`
- `retrieval/llm.py`
- `scripts/retrieval_eval.py`

## 1. Main Objective

For every evaluated query, produce one detailed scorecard entry that answers:

1. Was the right evidence retrieved?
2. Did the assembler keep the right evidence under the token budget?
3. Did the response mode match the question?
4. Was the final answer grounded in the evidence shown to the answer generator?
5. If the answer was weak, which stage most likely caused the weakness?

The output should be a per-response artifact, not only an aggregate average.

Aggregate scores are still useful, but the working unit for this validation layer is one query-response pair.

## 2. What This Validation Layer Is For

RAGAS should be used in CodeSeek for:

- per-response grounding review
- per-response retrieval quality review
- LLM-path answer quality measurement
- deterministic-answer audit support
- release-readiness spot checks
- investigation of regressions after retrieval changes

RAGAS should not be the only validation layer.

CodeSeek already has a deterministic retrieval eval suite in `scripts/retrieval_eval.py` that measures:

- `hit@k`
- `mrr@k`
- citation coverage
- expected file presence
- expected symbol presence
- expected framework/dependency presence
- expected no-answer behavior
- expected response-mode selection
- expected answer-term presence
- latency buckets

That suite remains the fast regression gate. RAGAS is the deeper per-response audit layer.

## 3. CodeSeek-Specific Constraint

The most important implementation rule is this:

RAGAS must evaluate the actual context seen by the answer path, not only the raw top-k retrieval candidates.

In CodeSeek, the final answer is shaped by several stages after search:

1. query understanding
2. search
3. expansion
4. assembly under a token budget
5. display-source filtering
6. optional two-layer source split
7. response-mode selection

Because of that, the RAGAS `contexts` field must be taken from the post-assembly context used by the answer path:

- deterministic answer paths should use the assembled deterministic context
- LLM answer paths should use the reasoning context passed toward the LLM path

It is not sufficient to evaluate:

- only `search()` output
- only Qdrant payload summaries
- only display-time source cards

If RAGAS scores a different context than the model actually saw, the scores will be misleading.

## 4. Where RAGAS Fits In The Current Pipeline

### 4.1 Ingestion

RAGAS is not the primary tool for ingestion validation.

Keep ingestion validation deterministic:

- parser correctness
- chunk boundaries
- overflow split compliance
- summary non-emptiness
- embedding dimension and NaN checks
- Qdrant count reconciliation

### 4.2 Query Understanding

RAGAS does not directly validate intent classification well.

Keep a labeled query-understanding dataset for:

- `primary_intent`
- extracted files
- extracted symbols
- extracted routes
- extracted env keys
- extracted dependency names

This should remain a deterministic accuracy check.

### 4.3 Search, Expansion, and Assembly

RAGAS is useful here.

Recommended metrics:

- `context_precision`
- `context_recall`

Interpretation in CodeSeek:

- low `context_precision`: too much noisy evidence survived search, expansion, or assembly
- low `context_recall`: key evidence was missed or dropped before final context assembly

### 4.4 Final Answer

RAGAS is most useful here.

Recommended metrics:

- `faithfulness`
- `answer_relevancy`
- `answer_correctness` when reliable ground truth exists

Interpretation in CodeSeek:

- low `faithfulness`: the answer contains claims not supported by the final assembled context
- low `answer_relevancy`: the answer may be grounded but still fails to answer the user’s question
- low `answer_correctness`: the response is factually wrong relative to the gold answer

## 5. Response Types To Validate

CodeSeek does not have only one answer path. Validation must record which answer path produced the response.

At minimum, every scorecard entry must capture one of these `response_mode` values:

- `code_excerpt`
- `architecture_summary`
- `overview_summary`
- `flow_summary`
- `symbol_deep_dive`
- `explanation_summary`
- `llm`
- `low_context`

Expected use:

- `llm` responses should use the full RAGAS metric set
- deterministic responses should still be logged in the same scorecard format, but some metrics may be marked `not_applicable`
- `low_context` responses should be tracked separately because a low score may be correct behavior when evidence is absent

## 6. Per-Response Scorecard Requirements

Each evaluated response should produce a detailed record with the following sections.

### 6.1 Request Identity

- `case_id`
- `query`
- `repo_root`
- `collection_name`
- `request_id`
- `timestamp_utc`

### 6.2 Query Understanding Snapshot

- `raw_query`
- `resolved_query`
- `primary_intent`
- `legacy_intent`
- `intent_scores`
- `entities`
- `is_followup`

### 6.3 Pipeline Path Metadata

- `response_mode`
- `latency_profile`
- `total_latency_ms`
- `backend_latency_ms`
- `provider_latency_ms`
- `stage_latency_ms`
- `evidence_confidence`
- `source_filter`

### 6.4 Retrieval Evidence Sets

Store all of the following separately so failures can be localized:

- `search_candidates`
- `expanded_candidates`
- `assembled_sources`
- `display_sources`
- `reasoning_sources`

Each source item should preserve:

- `relative_path`
- `symbol_name`
- `qualified_symbol`
- `chunk_type`
- `start_line`
- `end_line`
- `expansion_type`
- `score` if available
- `summary`
- `signature`

### 6.5 Final Answer Inputs

- `final_answer`
- `contexts`
- `context_token_count`
- `reasoning_context_token_count`
- `ground_truth`
- `ground_truth_sources`

`contexts` must contain the actual source excerpts or assembled text used by the answer path.

### 6.6 RAGAS Metrics

Each response should record:

- `context_precision`
- `context_recall`
- `faithfulness`
- `answer_relevancy`
- `answer_correctness`

Each metric should support three states:

- numeric score
- `not_applicable`
- `error`

### 6.7 Human Debug Notes

- `failure_stage_hint`
- `review_notes`
- `manual_override_label`

These are optional, but they are useful for triage.

## 7. Recommended Dataset Schema

The existing eval JSON files should not be discarded. Extend the current fixture shape instead of introducing a completely separate unrelated dataset format.

Recommended per-case fields:

```json
{
  "id": "cs-ragas-001",
  "query": "Trace how provider credentials are resolved for a query.",
  "expected_response_mode": "flow_summary",
  "expected_intent": "TRACE",
  "ground_truth": "Provider credentials are loaded from the provider store, the active credential is resolved, and the selected provider config is passed into query execution.",
  "ground_truth_sources": [
    {
      "relative_path": "retrieval/api_service.py",
      "symbol_name": "_query_impl"
    },
    {
      "relative_path": "retrieval/provider_store.py",
      "symbol_name": "get_active_provider_credential"
    }
  ],
  "expected_files": [
    "retrieval/api_service.py",
    "retrieval/provider_store.py"
  ],
  "expected_symbols": [
    "_query_impl",
    "get_active_provider_credential"
  ],
  "expected_answer_terms": [
    "provider credential",
    "active credential",
    "provider_config"
  ]
}
```

Why this shape:

- it preserves compatibility with `retrieval_eval.py`
- it adds the fields RAGAS needs
- it avoids maintaining two unrelated golden datasets

## 8. Per-Response Output Format

The main deliverable should be a machine-readable report where each response has its own full score entry.

Recommended output file:

- `backend/docs/retrieval_docs/eval_results_ragas_latest.json`

Recommended top-level structure:

```json
{
  "run_meta": {
    "dataset_name": "codeseek-ragas-v1",
    "repo_root": "/home/arch/DEV/CodeSeek/backend",
    "collection_name": "repository_chunks__local__codeseek",
    "generated_at_utc": "2026-06-05T10:30:00Z",
    "case_count": 24
  },
  "summary": {
    "context_precision_avg": 0.89,
    "context_recall_avg": 0.86,
    "faithfulness_avg": 0.91,
    "answer_relevancy_avg": 0.88,
    "answer_correctness_avg": 0.84
  },
  "responses": [
    {
      "case_id": "cs-ragas-001",
      "query": "Trace how provider credentials are resolved for a query.",
      "response_mode": "flow_summary",
      "primary_intent": "TRACE",
      "final_answer": "The provider credential lifecycle starts in `retrieval/api_service.py :: _query_impl` ...",
      "ragas": {
        "context_precision": 0.92,
        "context_recall": 0.88,
        "faithfulness": 0.96,
        "answer_relevancy": 0.90,
        "answer_correctness": 0.89
      },
      "contexts": [
        {
          "relative_path": "retrieval/api_service.py",
          "symbol_name": "_query_impl",
          "start_line": 100,
          "end_line": 230,
          "content": "..."
        }
      ],
      "ground_truth": "Provider credentials are loaded from the provider store ...",
      "ground_truth_sources": [
        {
          "relative_path": "retrieval/provider_store.py",
          "symbol_name": "get_active_provider_credential"
        }
      ],
      "latency": {
        "total_latency_ms": 312,
        "backend_latency_ms": 312,
        "provider_latency_ms": 0
      },
      "failure_stage_hint": "none"
    }
  ]
}
```

## 9. How To Interpret Scores Per Response

Per-response interpretation should be explicit so scorecards are actionable.

### 9.1 `context_precision`

High score means:

- the retrieved and assembled evidence is mostly relevant

Low score usually means:

- search admitted noisy candidates
- expansion added weak neighbors or dependency edges
- the assembler kept low-value context instead of the best evidence

### 9.2 `context_recall`

High score means:

- the final context includes the core evidence needed to answer

Low score usually means:

- search missed the right file or symbol
- exact entity promotion missed a strong cue
- expansion failed to pull in parent/callee/split-part support
- the assembler dropped an important chunk under the token budget

### 9.3 `faithfulness`

High score means:

- the answer’s claims are supported by the assembled context

Low score usually means:

- the answer path made unsupported claims
- the LLM generalized beyond the provided code
- a deterministic summary stitched together claims that were not fully backed by evidence

### 9.4 `answer_relevancy`

High score means:

- the answer actually addresses the user’s question

Low score usually means:

- the answer is grounded but off-target
- the wrong response mode was chosen
- the answer over-focused on nearby code details instead of the asked task

### 9.5 `answer_correctness`

High score means:

- the final answer agrees with a trusted gold answer

Low score usually means:

- the answer missed an important step or source
- the answer contradicted the expected behavior
- the ground truth itself may need review if the mismatch looks suspicious

## 10. Failure Stage Hint Rules

Each response record should include a simple `failure_stage_hint`.

Recommended values:

- `query_understanding`
- `search`
- `expand`
- `assemble`
- `source_filter`
- `response_mode_selection`
- `answer_generation`
- `ground_truth_gap`
- `none`

Suggested assignment logic:

- low `context_recall` with wrong files missing: `search`
- good search candidates but poor final context: `assemble`
- correct context but low `faithfulness`: `answer_generation`
- grounded but off-target answer: `response_mode_selection`
- impossible-to-score or unstable gold answer: `ground_truth_gap`

This does not need to be perfect. It only needs to speed up triage.

## 11. Recommended Thresholds

These should begin as reporting thresholds, not immediate hard CI blocks.

Suggested starting targets for the RAGAS layer:

| Metric | Target |
|---|---:|
| `context_precision_avg` | `>= 0.85` |
| `context_recall_avg` | `>= 0.85` |
| `faithfulness_avg` | `>= 0.90` |
| `answer_relevancy_avg` | `>= 0.85` |
| `answer_correctness_avg` | `>= 0.80` |

Per-response warning thresholds:

| Metric | Warning |
|---|---:|
| `context_precision` | `< 0.70` |
| `context_recall` | `< 0.70` |
| `faithfulness` | `< 0.80` |
| `answer_relevancy` | `< 0.75` |
| `answer_correctness` | `< 0.75` |

These should be reviewed after the first real baseline run.

## 12. Recommended Execution Strategy

Do not run the RAGAS layer against every fixture first.

Start with a smaller curated gold set:

- 20 to 40 cases
- mixed across `SYMBOL`, `TRACE`, `DEPENDENCY`, `OVERVIEW`, `ARCHITECTURE`, `CONFIG`, and `FOLLOWUP`
- biased toward the highest-value CodeSeek behaviors

Recommended first-wave case families:

- symbol lookup
- request/response trace
- auth/session lifecycle
- indexing/session creation flow
- provider credential lifecycle
- deployment/configuration flow
- architecture summary
- low-context / absent-evidence behavior

## 13. How This Should Work With Existing Eval Tooling

The current deterministic eval suite should remain the first gate.

Recommended order:

1. Run `scripts/retrieval_eval.py`
2. Check threshold output with `scripts/check_retrieval_metrics.py`
3. Run the RAGAS layer on the curated gold set
4. Review low-scoring per-response entries
5. Use the manual response checklist for final spot-checks

This gives three layers:

- fast regression gate
- deep per-response scoring
- human qualitative review

## 14. Reporting Requirements

The RAGAS report should support both summary and drill-down views.

Minimum summary fields:

- overall averages for every RAGAS metric
- averages by `primary_intent`
- averages by `response_mode`
- averages by `latency_profile`
- count of responses below warning threshold

Minimum drill-down fields:

- full answer text
- full evaluated context
- files and symbols used
- stage latencies
- response mode
- all raw metric scores
- failure-stage hint

The report is only useful if a reviewer can move from:

- "the average faithfulness dropped"

to:

- "these 3 TRACE responses lost faithfulness because expansion admitted noisy support chunks"

without re-running the system blindly.

## 15. Non-Goals

This design does not require:

- replacing the existing eval JSON fixtures
- removing deterministic evals
- validating ingestion only through RAGAS
- forcing all deterministic answer paths to behave like LLM answers
- making RAGAS a hard CI blocker on day one

## 16. Recommended Next Implementation Step

The first implementation step should be a dedicated `scripts/ragas_eval.py` runner that:

1. loads an extended eval fixture
2. runs the current pipeline through `run_query(..., return_meta=True)`
3. captures the real assembled contexts used by the answer path
4. computes per-response RAGAS metrics
5. writes a detailed JSON report with one full scorecard entry per response

If the implementation cannot yet capture the exact final contexts for a response mode, that response should be marked:

- `context_capture_status: "incomplete"`

and excluded from score comparisons until the capture is accurate.

## 17. Implementation Task List

This section is the execution checklist for adding RAGAS validation to CodeSeek.

The goal is not only to compute averages. The goal is to ship a reliable per-response validation artifact that shows:

- what the system answered
- what evidence it used
- how that answer scored
- why it scored that way

The checklist below is ordered so the implementation can be built incrementally without losing correctness.

### WS1 Dataset and Fixture Contract

- [x] define the first dedicated RAGAS fixture file, for example `docs/retrieval_docs/eval_codeseek_ragas_v1.json`
- [x] extend the current eval-case schema with:
  - `ground_truth`
  - `ground_truth_sources`
  - `expected_intent`
  - optional `expected_context_terms`
  - optional `notes`
- [x] keep backward compatibility with the current `retrieval_eval.py` fixture shape so one case file can support both deterministic evals and RAGAS evals
- [x] write 20-40 initial gold cases covering:
  - `SYMBOL`
  - `TRACE`
  - `DEPENDENCY`
  - `OVERVIEW`
  - `ARCHITECTURE`
  - `CONFIG`
  - `FOLLOWUP`
  - `LOW_CONTEXT`
- [x] make sure each gold case has a short but explicit ground-truth answer rather than only file expectations
- [x] make sure each gold case includes source-level evidence identifiers, not only file paths, when the answer depends on a specific function or symbol
- [x] add at least 3 negative or absent-evidence cases so low-context behavior is measured intentionally
- [x] document case-writing rules in this file or a companion fixture README so future additions stay consistent

### WS2 Pipeline Instrumentation for Context Capture

- [x] add a dedicated capture path in `retrieval/main.py` so eval mode can return the exact assembled contexts used by the final answer path
- [x] capture deterministic-path assembled context separately from LLM reasoning context
- [x] capture `search_candidates`, `expanded_candidates`, `assembled_sources`, `display_sources`, and `reasoning_sources` in a stable serialized form
- [x] preserve file/symbol/line metadata for every captured source item
- [x] include `response_mode`, `primary_intent`, `resolved_query`, and `entities` in the returned eval metadata
- [x] include `context_token_count` and `reasoning_context_token_count` in the returned eval metadata
- [x] mark context capture explicitly when incomplete with a field such as `context_capture_status`
- [x] avoid changing normal API output shape for non-eval calls
- [x] add tests proving that eval capture returns the same context that the answer path actually used

### WS3 RAGAS Runner Implementation

- [x] create `backend/scripts/ragas_eval.py`
- [x] load the extended fixture schema from disk
- [x] run queries through `run_query(..., return_meta=True)` so the real pipeline is evaluated rather than a mocked shortcut
- [x] build the RAGAS dataset rows from:
  - `question`
  - `answer`
  - `contexts`
  - `ground_truth`
- [x] ensure the `contexts` field contains actual assembled source excerpts or assembled text blocks, not only metadata summaries
- [x] support per-case skipping when a required field is missing, with explicit reason logging
- [x] support provider-backed runs and provider-less runs without changing the fixture format
- [x] write a machine-readable output file, for example `docs/retrieval_docs/eval_results_ragas_latest.json`
- [x] print a concise CLI summary with aggregate metrics and counts of low-scoring responses

### WS4 Per-Response Scorecard Output

- [x] define the final per-response JSON schema for the report
- [x] include request identity fields:
  - `case_id`
  - `query`
  - `request_id`
  - `timestamp_utc`
  - `repo_root`
  - `collection_name`
- [x] include query understanding fields:
  - `raw_query`
  - `resolved_query`
  - `primary_intent`
  - `legacy_intent`
  - `intent_scores`
  - `entities`
  - `is_followup`
- [x] include pipeline metadata fields:
  - `response_mode`
  - `latency_profile`
  - `stage_latency_ms`
  - `backend_latency_ms`
  - `provider_latency_ms`
  - `evidence_confidence`
  - `source_filter`
- [x] include the full answer text and evaluated contexts in every response entry
- [x] include the ground-truth answer and ground-truth sources in every response entry
- [x] include all RAGAS metric values in every response entry
- [x] include `failure_stage_hint` in every response entry, even if the value is `none`
- [x] keep the schema stable enough that follow-up tools can diff two runs automatically

### WS5 Metric Semantics and Edge Cases

- [x] wire `context_precision`
- [x] wire `context_recall`
- [x] wire `faithfulness`
- [x] wire `answer_relevancy`
- [x] wire `answer_correctness` only for cases with valid ground truth
- [x] support three output states per metric:
  - numeric score
  - `not_applicable`
  - `error`
- [x] define how deterministic response modes should be scored when some RAGAS metrics are not meaningful
- [x] define how `low_context` responses should be scored and whether they should be excluded from some averages
- [x] define how provider failures, timeouts, or empty contexts should appear in the final report
- [x] add explicit metric-computation error messages to the output JSON instead of silently dropping failed cases

### WS6 Failure Attribution and Triage Hints

- [x] implement automatic `failure_stage_hint` assignment rules
- [x] classify likely search failures when ground-truth sources are absent from `search_candidates`
- [x] classify likely expansion failures when search finds enough evidence but expansion loses structural support
- [x] classify likely assembly failures when strong candidates exist but do not survive the token budget
- [x] classify likely source-filter failures when the reasoning set is strong but display sources are misleadingly thin
- [x] classify likely answer-generation failures when context quality is good but `faithfulness` or `answer_relevancy` is poor
- [x] classify likely `ground_truth_gap` cases when the mismatch appears to come from the dataset itself
- [x] document that these hints are triage aids, not absolute truth

### WS7 Aggregate Views and Breakdown Reporting

- [x] compute overall averages for each RAGAS metric
- [x] compute averages by `primary_intent`
- [x] compute averages by `response_mode`
- [x] compute averages by `latency_profile`
- [x] count responses below warning thresholds per metric
- [x] list the lowest-scoring responses for:
  - `context_precision`
  - `context_recall`
  - `faithfulness`
  - `answer_relevancy`
  - `answer_correctness`
- [x] include a small pass/fail summary based on warning thresholds
- [x] preserve per-response detail even when aggregate summaries are printed

### WS8 Thresholds and Rollout Policy

- [x] encode initial reporting thresholds for aggregate metrics
- [x] encode per-response warning thresholds
- [x] keep the first rollout in report-only mode rather than blocking CI immediately
- [x] define the conditions required before any hard CI gate is introduced
- [x] document how to review a bad run:
  - inspect low-scoring responses first
  - inspect failure-stage hints second
  - inspect aggregate trends last
- [x] document when a low score is acceptable, especially for absent-evidence and low-context cases

### WS9 Tests and Validation of the Validation Layer

- [x] add unit tests for fixture parsing
- [x] add unit tests for scorecard serialization
- [x] add unit tests for `failure_stage_hint` assignment
- [x] add unit tests for metric-state handling:
  - numeric
  - `not_applicable`
  - `error`
- [x] add tests proving that the captured `contexts` match the final answer path inputs
- [x] add tests for deterministic response modes
- [x] add tests for `llm` response mode
- [x] add tests for `low_context` response mode
- [x] run the current deterministic retrieval eval suite before and after integration to verify no regression in the existing evaluator

### WS10 Documentation and Operating Procedure

- [x] add a short runbook section showing how to execute `scripts/ragas_eval.py`
- [x] document the expected output file location
- [x] document how to add a new gold case
- [x] document how to interpret each RAGAS metric in CodeSeek terms
- [x] document how to investigate a low-scoring response using the scorecard fields
- [x] document when to use:
  - `retrieval_eval.py`
  - `ragas_eval.py`
  - the manual response-review checklist
- [x] keep this design doc updated when the implementation diverges from the planned schema or workflow

### Runbook

Use the new runner from the backend root:

```bash
./.venv/bin/python scripts/ragas_eval.py \
  --eval-file docs/retrieval_docs/eval_codeseek_ragas_v1.json \
  --output-json docs/retrieval_docs/eval_results_ragas_latest.json \
  --output-md docs/retrieval_docs/eval_results_ragas_latest.md
```

If the dense embedding model is not cached locally, use the offline-safe variant:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 RETRIEVAL_ENABLE_DENSE=0 RETRIEVAL_ENABLE_LEXICAL=1 \
  ./.venv/bin/python scripts/ragas_eval.py \
  --eval-file docs/retrieval_docs/eval_codeseek_ragas_v1.json \
  --output-json docs/retrieval_docs/eval_results_ragas_latest.json \
  --output-md docs/retrieval_docs/eval_results_ragas_latest.md \
  --family-baseline-out docs/retrieval_docs/ragas_family_baseline_latest.json
```

Check thresholds against the generated JSON report:

```bash
./.venv/bin/python scripts/check_ragas_metrics.py \
  --report docs/retrieval_docs/eval_results_ragas_latest.json
```

Check the curated human-reviewed benchmark:

```bash
./.venv/bin/python scripts/check_ragas_human_review.py \
  --report docs/retrieval_docs/eval_results_ragas_latest.json \
  --benchmark docs/retrieval_docs/ragas_human_review_benchmark_v1.json
```

Capture or compare historical family baselines:

```bash
./.venv/bin/python scripts/ragas_eval.py \
  --eval-file docs/retrieval_docs/eval_codeseek_ragas_v1.json \
  --output-json docs/retrieval_docs/eval_results_ragas_latest.json \
  --output-md docs/retrieval_docs/eval_results_ragas_latest.md \
  --family-baseline-out docs/retrieval_docs/ragas_family_baseline_latest.json
```

```bash
./.venv/bin/python scripts/ragas_eval.py \
  --eval-file docs/retrieval_docs/eval_codeseek_ragas_v1.json \
  --output-json docs/retrieval_docs/eval_results_ragas_latest.json \
  --output-md docs/retrieval_docs/eval_results_ragas_latest.md \
  --family-baseline docs/retrieval_docs/ragas_family_baseline_latest.json
```

The baseline snapshot can be created with `--family-baseline-out` and then reused with `--family-baseline` to compare family-level drift across runs.

The frontend exposes the latest scorecard bundle through `GET /api/v1/ragas/latest` and the main app shows it behind the `RAGAS` button in the top bar.

The baseline snapshot stores per-family averages for `primary_intent` and `response_mode` so later runs can compare family-specific drift without hand-built spreadsheets.

To add a new gold case:

- copy an existing case shape from `eval_codeseek_ragas_v1.json`
- add a concise `ground_truth`
- add `ground_truth_sources` with file and symbol anchors when possible
- keep `expected_intent` and `expected_response_mode` aligned with the actual route you expect CodeSeek to take
- add `notes` when the case has a special interpretation or a known edge condition

Use the deterministic eval suite when you only need retrieval regression coverage:

- `scripts/retrieval_eval.py` for fast hit@k, MRR, citation coverage, and latency checks
- `scripts/ragas_eval.py` for per-response grounding and answer-quality scoring
- `manual_response_review_checklist.md` for final human review of formatting, tone, and usefulness

### WS11 Optional Future Extensions

- [x] add HTML or markdown report rendering on top of the JSON artifact once the JSON schema is stable
- [x] add trend comparison between two RAGAS runs so regressions can be reviewed without manual diffing
- [x] add per-family historical baselines after enough runs exist
- [x] add UI surfacing for per-response validation details only after the backend report format is stable
- [x] consider a small curated human-reviewed benchmark set for release signoff if automated scores and manual quality audits diverge
