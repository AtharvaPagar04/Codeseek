# RAGAS Evaluation Usage

This document outlines how to run LLM-based RAGAS evaluation on CodeSeek's retrieval and answer traces.

## Preconditions
- **Answer traces must exist**: Ensure answer trace logging is enabled via `ENABLE_ANSWER_TRACE_LOGGING=1` during query execution. The traces are stored in JSONL format.
- **RAGAS dependencies must be installed**:
  ```bash
  pip install -r requirements.txt
  ```
- **Evaluator provider must be configured**: Export the necessary provider environment variables (e.g., `OPENAI_API_KEY`).

## Dry Run
A dry run validates the answer trace schema, loading capability, and normalizes candidate traces without contacting external API endpoints.
```bash
.venv/bin/python evals/ragas_eval.py \
  --input /tmp/codeseek_answer_traces.jsonl \
  --output /tmp/ragas_dry_latest.json \
  --dry-run
```

## Live Run
To execute actual LLM evaluation:
```bash
.venv/bin/python evals/ragas_eval.py \
  --input /tmp/codeseek_answer_traces.jsonl \
  --output /tmp/ragas_live_latest.json \
  --limit 20 \
  --allow-no-ground-truth
```

## OpenAI Evaluator Mode
Configure the environment variables to use OpenAI:
```bash
export OPENAI_API_KEY="your-api-key"
export RAGAS_EVALUATOR_PROVIDER=openai
export RAGAS_EVALUATOR_MODEL=gpt-4o-mini
```

## Ollama Evaluator Mode

### Requirements
- Ollama running locally.
- Evaluator chat/judgement model pulled (e.g. `qwen2.5-coder:3b`).
- Embedding model pulled (e.g. `nomic-embed-text`).

### Running the health check and evaluation:
You can verify the evaluator and embedding models are reachable in Ollama before starting:
```bash
ollama list
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:3b
```

To run a dry-run with a health check:
```bash
.venv/bin/python evals/ragas_eval.py \
  --input /tmp/codeseek_answer_traces.jsonl \
  --output /tmp/ragas_ollama_dry_health.json \
  --dry-run \
  --evaluator-provider ollama \
  --evaluator-model qwen2.5-coder:3b \
  --embedding-model nomic-embed-text \
  --check-evaluator-health
```

To run a live local evaluation on a small sample:
```bash
RAGAS_EVALUATOR_PROVIDER=ollama \
RAGAS_EVALUATOR_MODEL=qwen2.5-coder:3b \
RAGAS_EMBEDDING_MODEL=nomic-embed-text \
OLLAMA_BASE_URL=http://localhost:11434 \
.venv/bin/python evals/ragas_eval.py \
  --input /tmp/codeseek_answer_traces.jsonl \
  --output /tmp/ragas_ollama_latest.json \
  --limit 1 \
  --allow-no-ground-truth
```

### Notes
* Local evaluator scores may differ from OpenAI evaluator scores due to differences in model capability.
* Keep the evaluation limit small (`--limit`) at first to keep local inference times low.
* Local evaluator evaluations require Ragas to wrap LangChain Ollama endpoints defensively. If a metric fails or returns `NaN`, the run will report a `PARTIAL` or `ERROR` status.

## Local Ollama Stability Options

To improve execution stability when using local models (e.g., Ollama), RAGAS evaluation supports granular timeout, concurrency (worker count), retry controls, and metric selection.

### Command Line Arguments
- `--ragas-timeout SECONDS`: The timeout in seconds for evaluation requests. (Defaults: 600s for Ollama, 180s for OpenAI).
- `--ragas-max-workers N`: The maximum number of concurrent threads/workers to run evaluation. (Defaults: 1 for Ollama to run serially, 4 for OpenAI).
- `--ragas-max-retries N`: The maximum number of retries for failed metric evaluation calls. (Defaults: 1 for Ollama, 3 for OpenAI).
- `--metrics METRIC_LIST`: A comma-separated list of metrics to execute. Supported values: `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`. (Default: `faithfulness,answer_relevancy,context_precision`).

### Environment Variables
These settings can also be configured using the following environment variables:
- `RAGAS_TIMEOUT_SECONDS`
- `RAGAS_MAX_WORKERS`
- `RAGAS_MAX_RETRIES`
- `RAGAS_METRICS`

### CLI Priority
Explicit command line arguments take priority over environment variables, which in turn take priority over provider-specific defaults.

### Single Metric Calibration Run Example
To run calibration using only the `faithfulness` metric, with serial execution and a high timeout to prevent local model timeouts:
```bash
.venv/bin/python evals/ragas_calibration.py \
  --provider ollama \
  --evaluator-model qwen2.5-coder:3b \
  --embedding-model nomic-embed-text \
  --metrics faithfulness \
  --ragas-timeout 600 \
  --ragas-max-workers 1 \
  --ragas-max-retries 1
```

## Session Binding Guard

To prevent calibration tests from executing and saving traces against the wrong repository session (which can overwrite legitimate traces and cause false retrieval failures), `ragas_calibration.py` provides a session binding guard.

### CLI Options
- `--expected-repo-root PATH`: The absolute path to the expected repository root directory.
- `--expected-collection NAME`: The expected name of the Qdrant database collection.

### Guard Behavior
1. After resolving the session and before generating answers, the script compares the bound/actual `repo_root` and `collection` against the expected values (resolving both to absolute paths).
2. If a mismatch is detected, the run is terminated immediately:
   - Prints a mismatch error to stderr.
   - If `--summary-output` is configured, writes a summary report JSON indicating the mismatch error.
   - Exits with a non-zero status code.
   - No trace files are deleted, created, or overwritten, and `ragas_eval.py` is not executed.

## Output Report
The evaluation results are written to a structured JSON file.
- **Per-trace scores**: Contains metrics like `faithfulness`, `answer_relevancy`, and `context_precision` on each individual query run.
- **Aggregate summary**: Summary statistics including average scores, diagnostics (e.g. context lengths, citation counts), and errors.

## Why RAGAS is Separate from `retrieval_eval`
- **`retrieval_eval`**: Validates deterministic file/symbol/label correctness and index alignment. It runs on golden queries with strict, static expectations.
- **`ragas_eval`**: Checks semantic and synthesis answer quality (faithfulness, answer relevance, and context alignment) from dynamic query sessions using an evaluator LLM.

## RAGAS Evaluator Comparison

To compare RAGAS scores for the same frozen trace across different evaluator setups (e.g. OpenAI vs local models), see the [RAGAS Evaluator Comparison Guide](ragas_evaluator_compare.md).

