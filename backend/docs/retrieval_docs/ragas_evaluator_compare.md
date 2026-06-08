# RAGAS Evaluator Comparison Tool

The RAGAS Evaluator Comparison tool (`evals/ragas_evaluator_compare.py`) allows developers to compare RAGAS evaluation metrics for the same frozen trace file across multiple evaluator configurations (e.g., different providers, models, or embedding models). 

This is crucial for identifying whether low or null RAGAS scores are caused by LLM judge model limitations, metric parser instability, or actual underlying retrieval and answer generation problems.

---

## Command Line Interface

### Usage
```bash
.venv/bin/python evals/ragas_evaluator_compare.py \
  --input-traces evals/reports/ragas_calibration_traces.jsonl \
  --output-json ../evals/reports/ragas_evaluator_compare_latest.json \
  --output-md ../evals/reports/ragas_evaluator_compare_latest.md \
  --metrics answer_relevancy,context_precision,faithfulness \
  --evaluator ollama:qwen2.5-coder:3b:nomic-embed-text \
  --evaluator ollama:qwen-coder-7b-16k:nomic-embed-text \
  --evaluator openai:gpt-4o-mini:text-embedding-3-small
```

### Arguments
- `--input-traces PATH`: (Required) Path to the frozen input traces JSONL file (does not regenerate answers).
- `--output-json PATH`: (Required) Path to write the aggregated comparison JSON report.
- `--output-md PATH`: (Required) Path to write the aggregated comparison Markdown report.
- `--metrics LIST`: (Optional) Comma-separated list of metrics to evaluate (default: `faithfulness,answer_relevancy,context_precision,context_recall`).
- `--limit N`: (Optional) Limit the number of traces to evaluate.
- `--evaluator CONFIG`: (Required, repeatable) Evaluator configuration in the format `provider:model:embedding_model`.

---

## Under the Hood
1. **Trace Validation**: The script validates that the input trace file exists and contains valid traces with contexts.
2. **Subprocess Execution**: For each requested evaluator config, the comparison runner invokes `evals/ragas_eval.py` as a isolated subprocess with safe defaults:
   - `--ragas-timeout 600`
   - `--ragas-max-workers 1`
   - `--ragas-max-retries 1`
3. **Graceful Failures**: If a single evaluator fails or errors, it does not abort the entire comparison. Instead, the evaluator's row is marked as `status: ERROR` with the subprocess stderr captured under `errors`.
4. **Aggregation & Evaluation**: The runner aggregates execution times, status, score health, missing reports, errors, and metric averages from each run's output JSON into a consolidated report.

---

## Comparison Heuristics & Recommendations

The comparison runner automatically analyzes output trends to provide actionable recommendations:

- **Stability Selection**: Recommends the evaluator configuration with the fewest null scores and highest numeric score health.
- **Metric Mismatch Detector**: If `context_precision` remains `0.0` across all evaluators (both local and proprietary) while retrieval/context file hit diagnostics pass, it notes that this suggests a metric or reference dataset schema mismatch rather than a retrieval failure.
- **Faithfulness Validator**: If null scores or exceptions for `faithfulness` only occur on smaller local models (e.g. 3b/7b/8b) but not on stronger models, the runner suggests using a stronger evaluator model (like `gpt-4o-mini` or a 32b+ parameter local model) for that metric.

---

## Output Formats

### JSON Report Structure
```json
{
  "status": "PARTIAL",
  "input_traces": "evals/reports/ragas_calibration_traces.jsonl",
  "metrics_requested": ["answer_relevancy", "context_precision", "faithfulness"],
  "evaluators": [
    "ollama:qwen2.5-coder:3b:nomic-embed-text",
    "ollama:qwen-coder-7b-16k:nomic-embed-text"
  ],
  "summary": {
    "best_numeric_score_health": "ollama_qwen2_5_coder_3b_nomic_embed_text",
    "lowest_null_score_count": "ollama_qwen2_5_coder_3b_nomic_embed_text",
    "faithfulness_null_counts": {
      "ollama_qwen2_5_coder_3b_nomic_embed_text": 0,
      "ollama_qwen_coder_7b_16k_nomic_embed_text": 1
    },
    "context_precision_values": {
      "ollama_qwen2_5_coder_3b_nomic_embed_text": 0.0
    },
    "answer_relevancy_values": {
      "ollama_qwen2_5_coder_3b_nomic_embed_text": 0.9
    },
    "recommendation": "Evaluator 'ollama_qwen2_5_coder_3b_nomic_embed_text' has the fewest null scores (0) and is recommended as the most stable configuration. context_precision remains 0.0 across all evaluators; this suggests a metric/reference mismatch rather than retrieval failure."
  },
  "results": [
    {
      "evaluator_id": "ollama_qwen2_5_coder_3b_nomic_embed_text",
      "provider": "ollama",
      "model": "qwen2.5-coder:3b",
      "embedding_model": "nomic-embed-text",
      "command": [...],
      "return_code": 0,
      "duration_seconds": 23.4,
      "report_path": "...",
      "status": "PASS",
      "score_health": {
        "numeric_score_count": 3,
        "null_score_count": 0,
        "metrics_with_numeric_scores": ["answer_relevancy", "context_precision", "faithfulness"],
        "metrics_with_null_scores": []
      },
      "metrics_run": ["answer_relevancy", "context_precision", "faithfulness"],
      "metrics_skipped": {},
      "errors": [],
      "metric_averages": {
        "answer_relevancy": 0.9,
        "context_precision": 0.0,
        "faithfulness": 0.85
      },
      "ragas_runtime": {...}
    },
    ...
  ]
}
```

### Markdown Report
Generates a reader-friendly report including:
- Top-level comparison status
- Metric average and evaluator execution comparison table
- Captured error details per failed configuration
- Formatted heuristic recommendation and a copy-pasteable command to calibrate using the recommended settings.
