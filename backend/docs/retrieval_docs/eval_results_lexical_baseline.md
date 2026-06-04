# Lexical Retrieval Baseline Results

Date: 2026-06-04

Repo evaluated:

- `/home/arch/DEV/CodeSeek/backend`

Collection:

- `repository_chunks__local__backend`

Ingestion command:

```bash
PYTHONPATH=. \
RETRIEVAL_REPO_ROOT=/home/arch/DEV/CodeSeek/backend \
QDRANT_RECREATE_COLLECTION=1 \
INGESTION_ENABLE_INCREMENTAL_FILE_SKIP=0 \
./.venv/bin/python -m rag_ingestion.main /home/arch/DEV/CodeSeek/backend
```

Ingestion result:

- initial lexical baseline: `120` files parsed OK, `717` chunks generated/stored
- after structured non-code metadata extraction: `122` files parsed OK, `753` chunks generated/stored
- after repo-summary artifact generation: `123` files parsed OK, `763` chunks generated/stored

Eval file:

- `docs/retrieval_docs/eval_codeseek_exact_wording.json`

## Results

| Mode | Dense | Lexical | hit@10 | MRR@10 | Citation Coverage | Expected File | Expected Symbol | Expected Framework | Expected Dependency | Expected No-Answer |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dense baseline | on | off | 0.500 | 0.375 | 0.812 | 0.500 | 1.000 | 1.000 | 0.875 | 1.000 |
| dense + exact entity promotion | on | off | 0.750 | 0.454 | 0.812 | 0.688 | 1.000 | 0.875 | 0.875 | 1.000 |
| dense + exact entity + structured metadata | on | off | 0.750 | 0.385 | 0.812 | 0.688 | 1.000 | 1.000 | 0.875 | 1.000 |
| dense + exact entity + structured metadata + repo summary | on | off | 0.750 | 0.383 | 0.812 | 0.688 | 1.000 | 1.000 | 0.875 | 1.000 |
| dense + lexical | on | on | 0.500 | 0.292 | 0.812 | 0.500 | 1.000 | 1.000 | 0.875 | 1.000 |
| dense + lexical + structured metadata | on | on | 0.750 | 0.368 | 0.812 | 0.688 | 1.000 | 1.000 | 0.875 | 1.000 |
| lexical only | off | on | 0.375 | 0.103 | 0.625 | 0.250 | 1.000 | 0.875 | 0.750 | 1.000 |

## Interpretation

The first lexical retrieval implementation is useful as a feature-flagged recall path, but it should remain disabled by default.

Observed behavior:

- lexical retrieval did not improve `hit@10` on this exact-wording eval
- lexical retrieval reduced MRR when combined with dense retrieval
- lexical-only retrieval was weaker than dense baseline
- bounded `content_excerpt` payloads are now available for lexical indexing, but BM25 ranking still needs better query/entity handling before default rollout
- scored intent/entity extraction plus exact entity promotion improved the dense-only default path from `0.500` to `0.750` hit@10 without enabling lexical retrieval
- structured non-code metadata preserved `0.750` hit@10 and improved `expected_framework_score` to `1.000`, but did not improve MRR on this small exact-wording eval
- lexical retrieval still should remain disabled by default after structured metadata, because it did not improve hit@10 and slightly reduced MRR versus the lexical-off default path

Current decision:

- keep `RETRIEVAL_ENABLE_LEXICAL=0` by default
- keep `RETRIEVAL_ENABLE_SCORED_INTENT=1` by default
- keep the feature flag and tests in place
- do not tune weighted fusion until broader eval coverage exists

Likely next improvement:

- connect structured metadata to repo-summary generation and overview/tech-stack answer paths
- add broader multi-repo eval cases before considering lexical default enablement
- investigate the remaining misses for `RETRIEVAL_ENABLE_LEXICAL` and `BAAI/bge-small-en-v1.5`
