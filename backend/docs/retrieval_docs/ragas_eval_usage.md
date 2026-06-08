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

## Output Report
The evaluation results are written to a structured JSON file.
- **Per-trace scores**: Contains metrics like `faithfulness`, `answer_relevancy`, and `context_precision` on each individual query run.
- **Aggregate summary**: Summary statistics including average scores, diagnostics (e.g. context lengths, citation counts), and errors.

## Why RAGAS is Separate from `retrieval_eval`
- **`retrieval_eval`**: Validates deterministic file/symbol/label correctness and index alignment. It runs on golden queries with strict, static expectations.
- **`ragas_eval`**: Checks semantic and synthesis answer quality (faithfulness, answer relevance, and context alignment) from dynamic query sessions using an evaluator LLM.
