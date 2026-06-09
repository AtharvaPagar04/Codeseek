# RAGAS Calibration Summary Report

- **Status**: PASS
- **Total Queries**: 5
- **Traces Generated**: 5
- **RAGAS Evaluator**: ollama (qwen2.5-coder:3b)

## Summary Metrics

### RAGAS Averages
- **Faithfulness**: None
- **Answer Relevancy**: 0.8599
- **Context Precision**: 0.0

### Deterministic Diagnostics Averages
- **Avg Answer Length (chars)**: 324.0
- **Avg Context Count**: 4.8
- **Expected File Hit Rate**: 100.0%
- **Answer Mentions Expected File Rate**: 100.0%

## Query Details

### q004: Show me where Qdrant upsert happens

- **Top Context Files**:
  - `backend/rag_ingestion/stages/storage.py`
  - `backend/retrieval/query_processor.py`
  - `backend/retrieval/code_answers.py`

- **Diagnostics**:
  - Answer Length: 135 chars
  - Context Count: 3
  - Expected File Found in Contexts: `True`
  - Answer Mentions Expected File: `True`
  - Answer Mentions Any Top Context File: `True`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: 0.9246
  - Context Precision: 0.0

- **Interpretation**: `answer_too_short_for_ragas`

### q007: Where is the FastAPI app initialized?

- **Top Context Files**:
  - `backend/retrieval/api_service.py`
  - `backend/rag_ingestion/stages/summary.py`
  - `backend/retrieval/code_answers.py`
  - ... and 1 more

- **Diagnostics**:
  - Answer Length: 167 chars
  - Context Count: 4
  - Expected File Found in Contexts: `True`
  - Answer Mentions Expected File: `True`
  - Answer Mentions Any Top Context File: `True`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: 1.0
  - Context Precision: 0.0

- **Interpretation**: `answer_too_short_for_ragas`

### q008: Where is environment variable handling implemented?

- **Top Context Files**:
  - `backend/rag_ingestion/stages/parser.py`
  - `backend/retrieval/query_intent.py`
  - `backend/retrieval/config.py`
  - ... and 3 more

- **Diagnostics**:
  - Answer Length: 156 chars
  - Context Count: 6
  - Expected File Found in Contexts: `True`
  - Answer Mentions Expected File: `True`
  - Answer Mentions Any Top Context File: `True`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: 0.9936
  - Context Precision: 0.0

- **Interpretation**: `answer_too_short_for_ragas`

### q043: What does this repo do?

- **Top Context Files**:
  - `backend/retrieval/session_indexer.py`
  - `backend/retrieval/config.py`
  - `backend/scripts/cleanup_stale_workspaces.py`
  - ... and 3 more

- **Diagnostics**:
  - Answer Length: 735 chars
  - Context Count: 6
  - Expected File Found in Contexts: `False`
  - Answer Mentions Expected File: `False`
  - Answer Mentions Any Top Context File: `True`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: 0.7514
  - Context Precision: 0.0

- **Interpretation**: `actual_answer_grounding_problems`

### q_auth: How does auth work?

- **Top Context Files**:
  - `backend/retrieval/api_service.py`
  - `backend/retrieval/auth_store.py`
  - `backend/retrieval/api_service.py`
  - ... and 2 more

- **Diagnostics**:
  - Answer Length: 427 chars
  - Context Count: 5
  - Expected File Found in Contexts: `False`
  - Answer Mentions Expected File: `False`
  - Answer Mentions Any Top Context File: `True`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: 0.63
  - Context Precision: 0.0

- **Interpretation**: `actual_answer_grounding_problems`
