# RAGAS Judge Calibration Analysis Report v1

- **Overall Status**: `PASS`
- **Total Traces Analyzed**: 5
- **Metrics Evaluated**: answer_relevancy, context_precision

## Summary Table

| Metric | Numeric Scores | Null/NaN Scores | Total Evaluated |
| --- | --- | --- | --- |
| `answer_relevancy` | 2 | 3 | 5 |
| `context_precision` | 2 | 3 | 5 |

## Deterministic Context-File Diagnostics

These deterministic metrics are not RAGAS scores. They are local diagnostics used to explain whether RAGAS `context_precision` aligns with expected file retrieval.

- **Queries with Expected Files**: 3
- **Expected File Hit Rate**: 66.67% (2/3)
- **Mean Expected File Rank**: 1.00
- **Mean Reciprocal Rank (MRR)**: 1.0000
- **Mean Deterministic Expected-File Precision**: 0.5000

### Retrieval Tuning Recommendation
> [!WARNING]
> **Retrieval tuning is RECOMMENDED** because the following queries failed to retrieve expected context files: q008.

## Per-Query Details

### Query `q004`: "Show me where Qdrant upsert happens"
- **Category**: `code_location`
- **Expected Files**: `backend/rag_ingestion/stages/storage.py`
- **Expected File Context Hit**: `True`
- **Expected Context File Hit**: `True`
- **Expected Context File Rank**: `1`
- **Expected Context File Precision**: `0.5000`
- **Expected Context File Reciprocal Rank**: `1.0000`
- **Found Expected Files**: `backend/rag_ingestion/stages/storage.py`
- **Missing Expected Files**: *None*
- **Retrieved Files**: `rag_ingestion/stages/storage.py`, `retrieval/code_answers.py`
- **Expected Answer Terms**: `storage.py` (✓), `client.upsert` (✓), `Qdrant` (✓)
- **Scores**: answer_relevancy: `0.9194`, context_precision: `0.0000`
- **Interpretation**: **RAGAS context_precision disagrees with deterministic expected-file hit; possible RAGAS context_precision/code-location mismatch; expected file ranked first; semantically relevant**

### Query `q007`: "Where is the FastAPI app initialized?"
- **Category**: `code_location`
- **Expected Files**: `backend/retrieval/api_service.py`
- **Expected File Context Hit**: `True`
- **Expected Context File Hit**: `True`
- **Expected Context File Rank**: `1`
- **Expected Context File Precision**: `1.0000`
- **Expected Context File Reciprocal Rank**: `1.0000`
- **Found Expected Files**: `backend/retrieval/api_service.py`
- **Missing Expected Files**: *None*
- **Retrieved Files**: `retrieval/api_service.py`
- **Expected Answer Terms**: `FastAPI` (✓), `api_service.py` (✓)
- **Scores**: answer_relevancy: `0.9549`, context_precision: `0.0000`
- **Interpretation**: **RAGAS context_precision disagrees with deterministic expected-file hit; possible RAGAS context_precision/code-location mismatch; expected file ranked first; semantically relevant**

### Query `q008`: "Where is environment variable handling implemented?"
- **Category**: `config`
- **Expected Files**: `backend/retrieval/config.py`
- **Expected File Context Hit**: `False`
- **Expected Context File Hit**: `False`
- **Expected Context File Rank**: `N/A`
- **Expected Context File Precision**: `0.0000`
- **Expected Context File Reciprocal Rank**: `N/A`
- **Found Expected Files**: *None*
- **Missing Expected Files**: `backend/retrieval/config.py`
- **Retrieved Files**: *None*
- **Expected Answer Terms**: `config.py` (✗), `environment` (✗)
- **Scores**: answer_relevancy: `null/NaN`, context_precision: `null/NaN`
- **Interpretation**: **expected file missing from retrieved contexts**

### Query `q043`: "What does this repo do?"
- **Category**: `overview`
- **Expected Files**: *None*
- **Expected File Context Hit**: `True`
- **Expected Context File Hit**: `True`
- **Expected Context File Rank**: `N/A`
- **Expected Context File Precision**: `N/A`
- **Expected Context File Reciprocal Rank**: `N/A`
- **Found Expected Files**: *None*
- **Missing Expected Files**: *None*
- **Retrieved Files**: *None*
- **Expected Answer Terms**: `retrieval` (✗), `repository` (✗), `CodeSeek` (✗)
- **Scores**: answer_relevancy: `null/NaN`, context_precision: `null/NaN`
- **Interpretation**: **calibrated pass**

### Query `q_auth`: "How does auth work?"
- **Category**: `architecture`
- **Expected Files**: *None*
- **Expected File Context Hit**: `True`
- **Expected Context File Hit**: `True`
- **Expected Context File Rank**: `N/A`
- **Expected Context File Precision**: `N/A`
- **Expected Context File Reciprocal Rank**: `N/A`
- **Found Expected Files**: *None*
- **Missing Expected Files**: *None*
- **Retrieved Files**: *None*
- **Expected Answer Terms**: `auth` (✓), `session` (✓)
- **Scores**: answer_relevancy: `null/NaN`, context_precision: `null/NaN`
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
