# RAGAS Evaluator Comparison Report

- **Status**: PASS
- **Input Traces Path**: `evals/reports/ragas_calibration_traces.jsonl`
- **Metrics Requested**: faithfulness

## Evaluator Comparison Table

| Provider | Model | Status | Numeric Count | Null Count | Answer Relevancy | Context Precision | Faithfulness | Duration (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ollama | qwen2.5-coder:3b | PASS | 1 | 0 | - | - | 0.0000 | 119.72 |
| ollama | qwen-coder-7b-16k | PASS | 1 | 0 | - | - | 0.2000 | 159.56 |

## Errors Section

No errors encountered during evaluation runs.

## Recommendation Section

Evaluator 'ollama_qwen2_5_coder_3b_nomic_embed_text' has the fewest null scores (0) and is recommended as the most stable configuration.

## Suggested Next Command

To run the calibration pipeline with the recommended stable configuration, execute:
```bash
.venv/bin/python evals/ragas_calibration.py \
  --provider ollama \
  --evaluator-model qwen2.5-coder:3b \
  --embedding-model nomic-embed-text
```
