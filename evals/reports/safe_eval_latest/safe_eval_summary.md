# CodeSeek Safe Evaluation Run Summary

**Overall Status**: `PASS`
**Started At**: 2026-06-09T18:56:43.298960Z
**Finished At**: 2026-06-09T18:57:09.047247Z
**Duration**: 25.75 seconds
**Session ID**: `880b3bff7b924c9cb6476033a26a3db8`
**Expected Repo Root**: `/tmp/codeseek_repo_workspace/local/atharvapagar04_codeseek`
**Expected Collection**: `repository_chunks__local__atharvapagar04_codeseek`

## Execution Steps

| Step Name | Status | Return Code | Duration (seconds) |
|---|---|---|---|
| retrieval_eval | `PASS` | 0 | 14.63 |
| conversation_eval | `PASS` | 0 | 11.09 |
| eval_policy_summary | `PASS` | 0 | 0.03 |

## Gating Policy Summary

**Hard Gate Status**: `PASS`

### Hard Gate Failures

- None

### Warnings

- None

### Diagnostics

- None

### Recommendation

All gates passed successfully. CodeSeek meets the evaluation quality standards for release.

---

## Detailed Gating Policy Report

# CodeSeek Evaluation Policy and Gating Report

> [!NOTE]
> **Overall Gating Status: PASS**
> All gates passed successfully. Ready for release.

- **Hard Gate Status**: `PASS`

## Loaded Reports

| Report Name | Loaded |
| --- | --- |
| `retrieval_report` | ✓ Yes |
| `conversation_report` | ✓ Yes |
| `judge_calibration_report` | ✗ No |
| `ragas_report` | ✗ No |
| `evaluator_compare_report` | ✗ No |

## Hard Gate Failures
*No hard gate failures detected.*

## Warnings
*No warnings detected.*

## Diagnostic-only Observations
*No diagnostics captured.*

## Recommendation

All gates passed successfully. CodeSeek meets the evaluation quality standards for release.

## Policy Notes

### RAGAS `context_precision` Local Model Mismatch Policy
RAGAS `context_precision` must not fail or warn on current local retrieval quality. Evaluator comparison runs demonstrate that `context_precision` remains `0.0` across both `qwen2.5-coder:3b` and `qwen-coder-7b-16k` for code-location traces, even when local deterministic diagnostics show that the expected files were successfully retrieved at rank 1. This occurs because small local models struggle to correctly interpret and parse the code snippet relevance format requested by the RAGAS template. Therefore, all local `context_precision` findings are diagnostic-only.

### RAGAS `faithfulness` Local Model Instability Policy
Local 3B and 7B models frequently generate null/NaN scores for `faithfulness` due to output format parser issues. Consequently, null or fluctuating `faithfulness` scores on local evaluators are treated as diagnostic-only observations rather than blocking errors.

