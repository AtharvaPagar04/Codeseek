# RAGAS Evaluator Comparison Report

- **Status**: PASS
- **Input Traces Path**: `evals/reports/ragas_calibration_traces.jsonl`
- **Metrics Requested**: answer_relevancy, context_precision

## Evaluator Comparison Table

| Provider | Model | Status | Numeric Count | Null Count | Answer Relevancy | Context Precision | Faithfulness | Duration (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ollama | qwen2.5-coder:3b | PASS | 10 | 0 | 0.8481 | 0.0000 | - | 1211.95 |

## Errors Section

No errors encountered during evaluation runs.

## Recommendation Section

Evaluator 'ollama_qwen2_5_coder_3b_nomic_embed_text' has the fewest null scores (0) and is recommended as the most stable configuration. context_precision remains 0.0 across all evaluators; this suggests a metric/reference mismatch rather than retrieval failure.

## Suggested Next Command

To run the calibration pipeline with the recommended stable configuration, execute:
```bash
.venv/bin/python evals/ragas_calibration.py \
  --provider ollama \
  --evaluator-model qwen2.5-coder:3b \
  --embedding-model nomic-embed-text
```
