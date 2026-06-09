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
| `judge_calibration_report` | ✓ Yes |
| `ragas_report` | ✓ Yes |
| `evaluator_compare_report` | ✓ Yes |

## Hard Gate Failures
*No hard gate failures detected.*

## Warnings
*No warnings detected.*

## Diagnostic-only Observations
- **[INFO]** RAGAS faithfulness on small local models is unstable/null (diagnostic-only; faithfulness requires larger/stronger model for stable scoring)
- **[INFO]** context_precision remains 0.0 across all evaluators (diagnostic-only)

## Recommendation

All gates passed successfully. CodeSeek meets the evaluation quality standards for release.

## Policy Notes

### RAGAS `context_precision` Local Model Mismatch Policy
RAGAS `context_precision` must not fail or warn on current local retrieval quality. Evaluator comparison runs demonstrate that `context_precision` remains `0.0` across both `qwen2.5-coder:3b` and `qwen-coder-7b-16k` for code-location traces, even when local deterministic diagnostics show that the expected files were successfully retrieved at rank 1. This occurs because small local models struggle to correctly interpret and parse the code snippet relevance format requested by the RAGAS template. Therefore, all local `context_precision` findings are diagnostic-only.

### RAGAS `faithfulness` Local Model Instability Policy
Local 3B and 7B models frequently generate null/NaN scores for `faithfulness` due to output format parser issues. Consequently, null or fluctuating `faithfulness` scores on local evaluators are treated as diagnostic-only observations rather than blocking errors.

