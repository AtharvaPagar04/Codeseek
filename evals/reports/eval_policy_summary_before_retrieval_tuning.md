# CodeSeek Evaluation Policy and Gating Report

> [!WARNING]
> **Overall Gating Status: WARN**
> No hard gates failed, but soft warnings were triggered. Review is recommended.

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
- **[WARN]** expected context file rank is 3 (> 1) for query q008
- **[WARN]** missing expected answer term 'CodeSeek' for query q043
- **[WARN]** missing expected answer term 'repository' for query q043

## Diagnostic-only Observations
- **[INFO]** RAGAS context_precision is 0.0 (diagnostic-only; local code-location traces often default to 0.0 on small models)
- **[INFO]** RAGAS faithfulness on small local models is unstable/null (diagnostic-only; faithfulness requires larger/stronger model for stable scoring)
- **[INFO]** context_precision remains 0.0 across all evaluators (diagnostic-only)
- **[INFO]** evaluator disagreement between 3B and 7B models (diagnostic-only)

## Recommendation

Review the soft warnings. Verify if low answer relevancy or missing terms represent a real answer quality regression or acceptable variance.

## Policy Notes

### RAGAS `context_precision` Local Model Mismatch Policy
RAGAS `context_precision` must not fail or warn on current local retrieval quality. Evaluator comparison runs demonstrate that `context_precision` remains `0.0` across both `qwen2.5-coder:3b` and `qwen-coder-7b-16k` for code-location traces, even when local deterministic diagnostics show that the expected files were successfully retrieved at rank 1. This occurs because small local models struggle to correctly interpret and parse the code snippet relevance format requested by the RAGAS template. Therefore, all local `context_precision` findings are diagnostic-only.

### RAGAS `faithfulness` Local Model Instability Policy
Local 3B and 7B models frequently generate null/NaN scores for `faithfulness` due to output format parser issues. Consequently, null or fluctuating `faithfulness` scores on local evaluators are treated as diagnostic-only observations rather than blocking errors.

