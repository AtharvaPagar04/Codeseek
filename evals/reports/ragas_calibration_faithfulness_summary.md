# RAGAS Calibration Summary Report

- **Status**: PASS
- **Total Queries**: 5
- **Traces Generated**: 5
- **RAGAS Evaluator**: ollama (qwen2.5-coder:3b)

## Summary Metrics

### RAGAS Averages
- **Faithfulness**: 0.125
- **Answer Relevancy**: None
- **Context Precision**: None

### Deterministic Diagnostics Averages
- **Avg Answer Length (chars)**: 178.6
- **Avg Context Count**: 0.6
- **Expected File Hit Rate**: 0.0%
- **Answer Mentions Expected File Rate**: 67.0%

## Query Details

### q004: Show me where Qdrant upsert happens

- **Top Context Files**:
  - `rag_ingestion/stages/storage.py`
  - `retrieval/code_answers.py`

- **Diagnostics**:
  - Answer Length: 135 chars
  - Context Count: 2
  - Expected File Found in Contexts: `False`
  - Answer Mentions Expected File: `True`
  - Answer Mentions Any Top Context File: `True`

- **RAGAS Scores**:
  - Faithfulness: 0.25
  - Answer Relevancy: None
  - Context Precision: None

- **Interpretation**: `answer_too_short_for_ragas`

### q007: Where is the FastAPI app initialized?

- **Top Context Files**:
  - `retrieval/api_service.py`

- **Diagnostics**:
  - Answer Length: 167 chars
  - Context Count: 1
  - Expected File Found in Contexts: `False`
  - Answer Mentions Expected File: `True`
  - Answer Mentions Any Top Context File: `True`

- **RAGAS Scores**:
  - Faithfulness: 0.0
  - Answer Relevancy: None
  - Context Precision: None

- **Interpretation**: `answer_too_short_for_ragas`

### q008: Where is environment variable handling implemented?

- **Top Context Files**:
  - *None retrieved*

- **Diagnostics**:
  - Answer Length: 197 chars
  - Context Count: 0
  - Expected File Found in Contexts: `False`
  - Answer Mentions Expected File: `False`
  - Answer Mentions Any Top Context File: `False`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: None
  - Context Precision: None

- **Interpretation**: `answer_too_short_for_ragas`

### q043: What does this repo do?

- **Top Context Files**:
  - *None retrieved*

- **Diagnostics**:
  - Answer Length: 197 chars
  - Context Count: 0
  - Expected File Found in Contexts: `False`
  - Answer Mentions Expected File: `False`
  - Answer Mentions Any Top Context File: `False`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: None
  - Context Precision: None

- **Interpretation**: `answer_too_short_for_ragas`

### q_auth: How does auth work?

- **Top Context Files**:
  - *None retrieved*

- **Diagnostics**:
  - Answer Length: 197 chars
  - Context Count: 0
  - Expected File Found in Contexts: `False`
  - Answer Mentions Expected File: `False`
  - Answer Mentions Any Top Context File: `False`

- **RAGAS Scores**:
  - Faithfulness: None
  - Answer Relevancy: None
  - Context Precision: None

- **Interpretation**: `answer_too_short_for_ragas`
