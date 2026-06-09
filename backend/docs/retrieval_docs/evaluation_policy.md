# CodeSeek Evaluation Policy and Gating Rules (v1)

This document defines the formal gating policy for CodeSeek's evaluation suites. It classifies all evaluation findings into one of three tiers: **Hard Gates**, **Soft Warnings**, and **Diagnostic-only Observations**. 

This policy layer protects our deployment and validation pipeline by separating clear regressions (which fail the build/run) from fluctuating LLM-based evaluation metrics (which are kept as warnings or diagnostics).

---

## 1. Finding Classification Summary

### Hard Gates (FAIL or ERROR)
These conditions represent direct functionality regressions or clear evaluation failures. Any hard gate failure results in an overall status of **ERROR** for the run.
* **Retrieval Eval status is FAIL or ERROR**: Any regression in deterministic retrieval metrics (e.g. `file_hit@5` dropping below the configured gate) is blocked.
* **Conversation Eval status is FAIL or ERROR**: Multi-turn followup context mapping or intent classification failure.
* **Deterministic `expected_context_file_hit` is False**: A golden calibration query with a non-empty `expected_files` list failed to retrieve those files.
* **Exact Hit Regressions > 0**: A query that previously hit the target at a high rank now fails to do so.
* **Protected Hit Preservation < 100%**: Any drop in the retrieval of explicitly protected chunks when protected hits exist.
* **Empty Result Rate > 0**: Unless explicitly configured via `--allow-empty-results`, any query returning zero retrieved contexts is blocked.

### Soft Warnings (WARN)
These conditions represent potential degradations or minor quality regressions. They do not block the build or run, but they generate a status of **WARN** to alert reviewers.
* **Low `answer_relevancy`**: If the average or query-level `answer_relevancy` falls below the baseline threshold (default: `0.6`).
* **Degraded `expected_context_file_rank`**: The rank of the expected context file worsens or is greater than 1 for important source-location queries.
* **Missing Expected Answer Terms**: The generated answer is missing one or more required terms from the golden query's `expected_answer_contains` configuration.
* **Evaluator Instability / Null Scores**: The evaluator comparison shows that one model failed to evaluate a trace, returned null/NaN metrics, or exhibited unstable execution behavior.

### Diagnostic-only Observations (PASS)
These observations are kept purely for trend tracking and research. They **must not** change a run's status to WARN or ERROR.
* **RAGAS `context_precision` on code-location traces**: Often defaults to 0.0 on small models despite deterministic confirmation of correct retrieval.
* **RAGAS `faithfulness` on small local models**: Unstable due to strict JSON output parsing requirements of RAGAS templates when executed on local 3B/7B models.
* **Evaluator Disagreement**: Differences in scoring between 3B and 7B models.

---

## 2. Key Gating and RAGAS Policy Decisions

### The `context_precision` Local Model Mismatch
> [!IMPORTANT]
> **RAGAS `context_precision` must not fail or warn on current local retrieval quality.**
> 
> Evaluator comparison runs demonstrate that `context_precision` remains `0.0` across both `qwen2.5-coder:3b` and `qwen-coder-7b-16k` for code-location traces, even when local deterministic diagnostics show that the expected files were successfully retrieved at rank 1. This occurs because small local models struggle to correctly interpret and parse the code snippet relevance format requested by the RAGAS template. Therefore, all local `context_precision` findings are diagnostic-only.

### Local Model `faithfulness` Instability
> [!WARNING]
> Local 3B and 7B models frequently generate null/NaN scores for `faithfulness` due to output format parser issues. Consequently, null or fluctuating `faithfulness` scores on local evaluators are treated as diagnostic-only observations rather than blocking errors.

---

## 3. Policy Verification
Evaluation runs are aggregated and gated automatically using `eval_policy_summary.py`. This script reads the JSON reports from retrieval, conversation, calibration, and RAGAS evaluations, evaluates them against the criteria defined above, and generates a structured status report.
