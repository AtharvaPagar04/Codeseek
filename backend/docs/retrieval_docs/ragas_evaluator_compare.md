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
  --metrics answer_relevancy,context_precision \
  --evaluator ollama:qwen2.5-coder:3b:nomic-embed-text \
  --evaluator openai:gpt-4o-mini:text-embedding-3-small \
  --limit 2 \
  --verbose
```

### Arguments
- `--input-traces PATH`: (Required) Path to the frozen input traces JSONL file (does not regenerate answers).
- `--output-json PATH`: (Required) Path to write the aggregated comparison JSON report.
- `--output-md PATH`: (Required) Path to write the aggregated comparison Markdown report.
- `--metrics LIST`: (Optional) Comma-separated list of metrics to evaluate (default: `faithfulness,answer_relevancy,context_precision,context_recall`).
- `--limit N`: (Optional) Limit the number of traces to evaluate. Useful for quick validations (e.g., `--limit 1` or `--limit 2`).
- `--evaluator CONFIG`: (Required, repeatable) Evaluator configuration in the format `provider:model:embedding_model`.
- `--subprocess-timeout SECONDS`: (Optional) Subprocess timeout in seconds per evaluator run (default: `3600`).
- `--verbose`: (Optional) If provided, prints progress indicators, elapsed runtime heartbeats, and exact command invocations.

---

## Under the Hood
1. **Trace Validation**: The script validates that the input trace file exists and contains valid traces with contexts.
2. **Subprocess Execution**: For each requested evaluator config, the comparison runner invokes `evals/ragas_eval.py` as an isolated subprocess.
3. **Heartbeat / Progress Logging**: While the subprocess is running, the script streams periodic elapsed time updates (every 5 seconds) to stdout to avoid the process looking stuck.
4. **Timeout Handling**: Subprocesses are executed with the timeout specified by `--subprocess-timeout`. If a timeout occurs:
   - The subprocess is terminated/killed.
   - The evaluator status is marked as `ERROR`.
   - The `return_code` is set to `null` in the JSON report.
   - A `SUBPROCESS_TIMEOUT` error entry (including `timeout_seconds`) is appended to the results row.
   - The runner proceeds to the next evaluator configuration.
5. **Aggregation & Evaluation**: The runner aggregates execution times, status, score health, missing reports, errors, and metric averages from each run's output JSON into a consolidated report.

---

## Best Practices & Troubleshooting

### Recommend Metric Isolation
Evaluating multiple metrics simultaneously using local models can lead to high resource consumption and timeouts. It is highly recommended to isolate metric runs:
1. **Answer Relevancy first**: Run `--metrics answer_relevancy` to check response alignment. `answer_relevancy` is usually the most stable local metric.
2. **Context Precision second**: Run `--metrics context_precision` to inspect retrieval rank quality.
3. **Faithfulness separately**: Run `--metrics faithfulness` on its own. Faithfulness tends to be computationally heavy and prone to parser issues on smaller LLMs, so isolating it helps pinpoint judge limitations.

### Performance Warning for Local LLMs
Running comprehensive 3B vs 7B comparisons locally with all metrics enabled can be extremely slow and may take 60+ minutes depending on your CPU/GPU hardware.
- Use `--limit 1` or `--limit 2` to test configuration stability and verify that the models and embeddings load correctly before running full evaluations.
- Use `--subprocess-timeout` to bound execution times.

---

## Output Formats

### JSON Result Keys
Each row under `.results` contains detailed trace metadata:
- `timeout_seconds`: The timeout threshold set for the subprocess.
- `timed_out`: A boolean indicating whether the execution timed out (`true` or `false`).
- `stdout_tail`: The last 20 lines of standard output.
- `stderr_tail`: The last 20 lines of standard error.
- `return_code`: The subprocess exit code (or `null` if timed out).

### Markdown Report
Generates a reader-friendly report including:
- Top-level comparison status.
- Metric average and evaluator execution comparison table.
- Captured error details per failed configuration (including timeouts).
- Formatted heuristic recommendation and a copy-pasteable command to calibrate using the recommended settings.
