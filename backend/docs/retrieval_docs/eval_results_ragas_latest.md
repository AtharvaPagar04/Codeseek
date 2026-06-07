# CodeSeek RAGAS Validation Report

- Dataset: `codeseek-ragas-v1`
- Repo root: `/home/arch/DEV/CodeSeek/backend`
- Collection: `repository_chunks__local__backend`
- Generated: `2026-06-05T19:12:57.046695Z`
- Cases: `22`

## Summary

| Metric | Average |
|---|---:|
| `context_precision` | `0.2246` |
| `context_recall` | `0.3788` |
| `faithfulness` | `0.4823` |
| `answer_relevancy` | `0.4451` |
| `answer_correctness` | `0.1167` |

## Lowest Scores

### `context_precision`

| Case | Query | Mode | Score | Failure Stage |
|---|---|---|---:|---|
| `cs-ragas-003` | Which code invalidates the lexical index after ingestion? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-004` | Where is the qdrant-client dependency declared? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-005` | Where is FastAPI declared as a dependency? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-007` | Where is the submission-key endpoint implemented? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-010` | does this repository use COBOL or Fortran language? | `low_context` | `0.0000` | `response_mode_selection` |

### `context_recall`

| Case | Query | Mode | Score | Failure Stage |
|---|---|---|---:|---|
| `cs-ragas-002` | Where is RETRIEVAL_ENABLE_LEXICAL configured? | `llm` | `0.0000` | `search` |
| `cs-ragas-003` | Which code invalidates the lexical index after ingestion? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-004` | Where is the qdrant-client dependency declared? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-005` | Where is FastAPI declared as a dependency? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-007` | Where is the submission-key endpoint implemented? | `low_context` | `0.0000` | `response_mode_selection` |

### `faithfulness`

| Case | Query | Mode | Score | Failure Stage |
|---|---|---|---:|---|
| `cs-ragas-009` | architecture overview | `architecture_summary` | `0.0188` | `none` |
| `cs-ragas-001` | Where is CODESEEK_DATABASE_URL documented or used? | `llm` | `0.0232` | `none` |
| `cs-ragas-002` | Where is RETRIEVAL_ENABLE_LEXICAL configured? | `llm` | `0.0232` | `search` |
| `cs-ragas-008` | Where is BAAI/bge-small-en-v1.5 configured? | `llm` | `0.0232` | `none` |
| `cs-ragas-011` | what is the purpose of CODESEEK_APP_ENCRYPTION_KEY? | `llm` | `0.0232` | `expand` |

### `answer_relevancy`

| Case | Query | Mode | Score | Failure Stage |
|---|---|---|---:|---|
| `cs-ragas-001` | Where is CODESEEK_DATABASE_URL documented or used? | `llm` | `0.0000` | `none` |
| `cs-ragas-011` | what is the purpose of CODESEEK_APP_ENCRYPTION_KEY? | `llm` | `0.1049` | `expand` |
| `cs-ragas-002` | Where is RETRIEVAL_ENABLE_LEXICAL configured? | `llm` | `0.1274` | `search` |
| `cs-ragas-008` | Where is BAAI/bge-small-en-v1.5 configured? | `llm` | `0.1274` | `none` |
| `cs-ragas-014` | how does authentication cookie lifecycle work | `flow_summary` | `0.1933` | `assemble` |

### `answer_correctness`

| Case | Query | Mode | Score | Failure Stage |
|---|---|---|---:|---|
| `cs-ragas-001` | Where is CODESEEK_DATABASE_URL documented or used? | `llm` | `0.0000` | `none` |
| `cs-ragas-019` | Where are retrieval results merged and deduplicated? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-020` | Where are parent and callee expansions implemented? | `low_context` | `0.0000` | `response_mode_selection` |
| `cs-ragas-011` | what is the purpose of CODESEEK_APP_ENCRYPTION_KEY? | `llm` | `0.0408` | `expand` |
| `cs-ragas-003` | Which code invalidates the lexical index after ingestion? | `low_context` | `0.0500` | `response_mode_selection` |

## Per-Response Details

### `cs-ragas-001`

- Query: Where is CODESEEK_DATABASE_URL documented or used?
- Response mode: `llm`
- Failure stage: `none`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.2676` | `numeric` |
| `context_recall` | `0.5000` | `numeric` |
| `faithfulness` | `0.0232` | `numeric` |
| `answer_relevancy` | `0.0000` | `numeric` |
| `answer_correctness` | `0.0000` | `numeric` |

### `cs-ragas-002`

- Query: Where is RETRIEVAL_ENABLE_LEXICAL configured?
- Response mode: `llm`
- Failure stage: `search`
- Ground truth source count: `1`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.3042` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `0.0232` | `numeric` |
| `answer_relevancy` | `0.1274` | `numeric` |
| `answer_correctness` | `0.0784` | `numeric` |

### `cs-ragas-003`

- Query: Which code invalidates the lexical index after ingestion?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0500` | `numeric` |

### `cs-ragas-004`

- Query: Where is the qdrant-client dependency declared?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `1`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0690` | `numeric` |

### `cs-ragas-005`

- Query: Where is FastAPI declared as a dependency?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `1`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0667` | `numeric` |

### `cs-ragas-006`

- Query: Where is docker compose deployment described?
- Response mode: `flow_summary`
- Failure stage: `response_mode_selection`
- Ground truth source count: `4`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.5269` | `numeric` |
| `context_recall` | `1.0000` | `numeric` |
| `faithfulness` | `0.0442` | `numeric` |
| `answer_relevancy` | `0.3629` | `numeric` |
| `answer_correctness` | `0.1867` | `numeric` |

### `cs-ragas-007`

- Query: Where is the submission-key endpoint implemented?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `1`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0714` | `numeric` |

### `cs-ragas-008`

- Query: Where is BAAI/bge-small-en-v1.5 configured?
- Response mode: `llm`
- Failure stage: `none`
- Ground truth source count: `1`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.5269` | `numeric` |
| `context_recall` | `1.0000` | `numeric` |
| `faithfulness` | `0.0232` | `numeric` |
| `answer_relevancy` | `0.1274` | `numeric` |
| `answer_correctness` | `0.0870` | `numeric` |

### `cs-ragas-009`

- Query: architecture overview
- Response mode: `architecture_summary`
- Failure stage: `none`
- Ground truth source count: `4`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.5158` | `numeric` |
| `context_recall` | `0.5000` | `numeric` |
| `faithfulness` | `0.0188` | `numeric` |
| `answer_relevancy` | `0.2345` | `numeric` |
| `answer_correctness` | `0.0548` | `numeric` |

### `cs-ragas-010`

- Query: does this repository use COBOL or Fortran language?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.1053` | `numeric` |

### `cs-ragas-011`

- Query: what is the purpose of CODESEEK_APP_ENCRYPTION_KEY?
- Response mode: `llm`
- Failure stage: `expand`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.3619` | `numeric` |
| `context_recall` | `1.0000` | `numeric` |
| `faithfulness` | `0.0232` | `numeric` |
| `answer_relevancy` | `0.1049` | `numeric` |
| `answer_correctness` | `0.0408` | `numeric` |

### `cs-ragas-012`

- Query: walk me through backend request orchestration flow
- Response mode: `flow_summary`
- Failure stage: `none`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.4817` | `numeric` |
| `context_recall` | `0.5000` | `numeric` |
| `faithfulness` | `0.0738` | `numeric` |
| `answer_relevancy` | `0.3500` | `numeric` |
| `answer_correctness` | `0.4638` | `numeric` |

### `cs-ragas-013`

- Query: explain the auth session lifecycle flow
- Response mode: `flow_summary`
- Failure stage: `none`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.4042` | `numeric` |
| `context_recall` | `0.5000` | `numeric` |
| `faithfulness` | `0.0778` | `numeric` |
| `answer_relevancy` | `0.2899` | `numeric` |
| `answer_correctness` | `0.1263` | `numeric` |

### `cs-ragas-014`

- Query: how does authentication cookie lifecycle work
- Response mode: `flow_summary`
- Failure stage: `assemble`
- Ground truth source count: `3`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.2653` | `numeric` |
| `context_recall` | `0.3333` | `numeric` |
| `faithfulness` | `0.0778` | `numeric` |
| `answer_relevancy` | `0.1933` | `numeric` |
| `answer_correctness` | `0.1277` | `numeric` |

### `cs-ragas-015`

- Query: trace the indexing session creation flow
- Response mode: `flow_summary`
- Failure stage: `expand`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.4008` | `numeric` |
| `context_recall` | `1.0000` | `numeric` |
| `faithfulness` | `0.0809` | `numeric` |
| `answer_relevancy` | `0.4023` | `numeric` |
| `answer_correctness` | `0.2812` | `numeric` |

### `cs-ragas-016`

- Query: how does deployment configuration work
- Response mode: `flow_summary`
- Failure stage: `expand`
- Ground truth source count: `4`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.3781` | `numeric` |
| `context_recall` | `1.0000` | `numeric` |
| `faithfulness` | `0.0442` | `numeric` |
| `answer_relevancy` | `0.2419` | `numeric` |
| `answer_correctness` | `0.2785` | `numeric` |

### `cs-ragas-017`

- Query: explain provider credential lifecycle
- Response mode: `flow_summary`
- Failure stage: `none`
- Ground truth source count: `4`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.5076` | `numeric` |
| `context_recall` | `1.0000` | `numeric` |
| `faithfulness` | `0.0994` | `numeric` |
| `answer_relevancy` | `0.3567` | `numeric` |
| `answer_correctness` | `0.2353` | `numeric` |

### `cs-ragas-018`

- Query: Which retrieval stage performs dense vector search?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `1`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0645` | `numeric` |

### `cs-ragas-019`

- Query: Where are retrieval results merged and deduplicated?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `1`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0000` | `numeric` |

### `cs-ragas-020`

- Query: Where are parent and callee expansions implemented?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0000` | `numeric` |

### `cs-ragas-021`

- Query: Where is the Go parser implemented?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.0588` | `numeric` |

### `cs-ragas-022`

- Query: Where is the Rust ingestion parser defined?
- Response mode: `low_context`
- Failure stage: `response_mode_selection`
- Ground truth source count: `2`

| Metric | Value | State |
|---|---:|---|
| `context_precision` | `0.0000` | `numeric` |
| `context_recall` | `0.0000` | `numeric` |
| `faithfulness` | `1.0000` | `numeric` |
| `answer_relevancy` | `0.7000` | `numeric` |
| `answer_correctness` | `0.1212` | `numeric` |
