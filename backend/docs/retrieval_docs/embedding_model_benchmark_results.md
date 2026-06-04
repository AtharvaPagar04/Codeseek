# Embedding Model Benchmark Results

**Date**: 2026-06-05  
**Script**: `scripts/embedding_model_benchmark.py`  
**Fixture**: `docs/retrieval_docs/eval_codeseek_flow_phase1.json` (6 cases, live Qdrant collection)

---

## Models Compared

| Property | Current (`bge-small-en-v1.5`) | Alternative (`bge-base-en-v1.5`) |
| :--- | :---: | :---: |
| Dimensions | 384 | 768 |
| MTEB Score | 62.17 | 63.55 |
| MTEB Delta | — | **+1.38** |
| Encode latency (ms/query) | 22.5 | 7.7 |
| Memory peak (MB) | 6.7 | 8.0 |
| Memory delta | — | +1.3 MB |
| Re-ingestion required | No | **Yes** (384 → 768 dims) |

---

## Recall Results (Current Model — Live Qdrant)

| Fixture | Cases | hit@10 | MRR@10 |
| :--- | :---: | :---: | :---: |
| `eval_codeseek_flow_phase1` | 6 | **1.000** | **0.756** |

> **Note**: Alternative model recall comparison was skipped because its embedding dimension (768) does not match the stored collection vectors (384). Cosine similarity across mismatched dimensions produces invalid rankings. A valid comparison requires full collection re-ingestion with the alternative model.

---

## Cost / Tradeoff Analysis

### Why not switch now?

1. **Re-ingestion required**: Switching to `bge-base-en-v1.5` (768 dims) requires recreating the entire Qdrant collection and re-embedding all 1,212 chunks. This is a significant operational event for production multi-repo deployments.

2. **Encoding latency is already faster**: Surprisingly, `bge-base` encodes queries at **7.7 ms/query** vs. **22.5 ms/query** for `bge-small`. This is likely due to hardware-specific batch scheduling. This is a point in favour of the alternative if re-ingestion occurs.

3. **Memory overhead is marginal**: Only **+1.3 MB** peak load difference — not a concern.

4. **MTEB gain is small (+1.38)**: On the MTEB benchmark the base model scores 63.55 vs. 62.17 for small — a modest 2.2% relative improvement. For code retrieval specifically (which is more keyword/symbol-overlap-driven), this gap may be smaller than the MTEB average suggests.

5. **Current hit@10 is already 1.000**: The current model achieves perfect recall on the flow-phase1 eval set. There is no observable precision gap to close.

---

## Decision Gate

**Verdict: KEEP CURRENT — `BAAI/bge-small-en-v1.5` is retained as the default.**

Switching is not justified because:
- The current model already achieves `hit@10 = 1.000` on the primary eval fixture.
- The alternative requires a full re-ingestion of 1,212+ chunks per collection.
- The MTEB gain (+1.38) is within the margin where code-retrieval-specific recall may not improve.
- Switching should only be re-evaluated if automated evals reveal consistent hit@10 failures not solvable through retrieval logic improvements.

---

## Conditions for Re-evaluation

Revisit this decision if any of the following occur:

- [ ] Weighted `hit@k` across the multi-repo suite falls below `0.85` on a sustained basis.
- [ ] A new eval fixture covering cross-file or cross-language queries reveals consistent misses.
- [ ] A same-dimension alternative (384-dim) with higher MTEB score becomes available (no re-ingestion needed).
- [ ] Ingestion pipeline is refactored to support hot-swap model upgrades transparently.

---

## Raw Results File

See [`embedding_model_benchmark_results.json`](./embedding_model_benchmark_results.json) for the full per-case breakdown.
