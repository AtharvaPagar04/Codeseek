# RAGAS Judge Calibration Analysis Report v1

- **Overall Status**: `PASS`
- **Total Traces Analyzed**: 5
- **Metrics Evaluated**: faithfulness

## Summary Table

| Metric | Numeric Scores | Null/NaN Scores | Total Evaluated |
| --- | --- | --- | --- |
| `faithfulness` | 2 | 3 | 5 |

### Retrieval Tuning Recommendation
> [!WARNING]
> **Retrieval tuning is RECOMMENDED** because the following queries failed to retrieve expected context files: q004, q007, q008.

## Per-Query Details

### Query `q004`: "Show me where Qdrant upsert happens"
- **Category**: `code_location`
- **Expected Files**: `backend/rag_ingestion/stages/storage.py`
- **Expected File Context Hit**: `False`
- **Retrieved Files**: `rag_ingestion/stages/storage.py`, `retrieval/code_answers.py`
- **Expected Answer Terms**: `storage.py` (✓), `client.upsert` (✓), `Qdrant` (✓)
- **Scores**: faithfulness: `0.2500`
- **Interpretation**: **calibrated pass**

### Query `q007`: "Where is the FastAPI app initialized?"
- **Category**: `code_location`
- **Expected Files**: `backend/retrieval/api_service.py`
- **Expected File Context Hit**: `False`
- **Retrieved Files**: `retrieval/api_service.py`
- **Expected Answer Terms**: `FastAPI` (✓), `api_service.py` (✓)
- **Scores**: faithfulness: `0.0000`
- **Interpretation**: **calibrated pass**

### Query `q008`: "Where is environment variable handling implemented?"
- **Category**: `config`
- **Expected Files**: `backend/retrieval/config.py`
- **Expected File Context Hit**: `False`
- **Retrieved Files**: *None*
- **Expected Answer Terms**: `config.py` (✗), `environment` (✗)
- **Scores**: faithfulness: `null/NaN`
- **Interpretation**: **calibrated pass**

### Query `q043`: "What does this repo do?"
- **Category**: `overview`
- **Expected Files**: *None*
- **Expected File Context Hit**: `True`
- **Retrieved Files**: *None*
- **Expected Answer Terms**: `retrieval` (✗), `repository` (✗), `CodeSeek` (✗)
- **Scores**: faithfulness: `null/NaN`
- **Interpretation**: **calibrated pass**

### Query `q_auth`: "How does auth work?"
- **Category**: `architecture`
- **Expected Files**: *None*
- **Expected File Context Hit**: `True`
- **Retrieved Files**: *None*
- **Expected Answer Terms**: `auth` (✓), `session` (✓)
- **Scores**: faithfulness: `null/NaN`
- **Interpretation**: **calibrated pass**

## Known Local Evaluator Limitations

When running RAGAS evaluation locally with Ollama using smaller models like `qwen2.5-coder:3b`:
1. **Faithfulness Instability**: Faithfulness scoring is highly unstable and frequently fails due to `RagasOutputParserException` or output formats that do not comply with RAGAS JSON expectations, resulting in `NaN` scores.
2. **Context Precision 0.0**: `context_precision` often defaults to `0.0` even when deterministic expected files are retrieved. This happens because the small model struggles to correctly rank or parse the code snippets' exact relevance mapping within the context layout requested by the RAGAS template.
3. **Context Length Constraints**: Local 3B models have constrained context windows and processing speeds, which can cause timeout issues under parallel load.

## Recommended Stable Local Smoke Metrics

- **Recommended stable local smoke metrics command**:
  ```bash
  --metrics answer_relevancy,context_precision
  ```
- **Recommendation on Faithfulness**:
  > [!IMPORTANT]
  > Faithfulness should be run separately or with a stronger judge (e.g. `qwen2.5-coder:32b`, `llama3:70b`, or commercial APIs like OpenAI GPT-4o). Running faithfulness on `qwen2.5-coder:3b` is not recommended for stable CI pipeline gates.
