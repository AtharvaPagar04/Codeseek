# Lexical Retrieval Layer Benchmark Results

**Date**: 2026-06-05  
**Script**: `scripts/lexical_layer_benchmark.py`  
**Collection**: `repository_chunks__local__backend` (1,212 chunks)  
**Cases**: 16 across 4 query families — SYMBOL, DEPENDENCY, OVERVIEW, SEMANTIC

---

## Index Build Cost (cold start, paid once per worker)

| Metric | Value |
| :--- | :---: |
| Documents indexed | 1,212 |
| Build time | **1,167 ms** |
| Peak memory | **41.1 MB** |
| Subsequent queries | BM25 in-process (no network) |

> Build time is paid once at first query after startup. All subsequent queries use the cached in-memory index.

---

## Recall Results by Query Family

| Family | n | hit@10 Dense | hit@10 Lexical | Δhit | Lat delta |
| :--- | :---: | :---: | :---: | :---: | :---: |
| SYMBOL | 5 | **1.000** | 0.600 | **▼ −0.400** | −14.6 ms |
| DEPENDENCY | 4 | 0.500 | 0.500 | = 0.000 | +72.8 ms |
| OVERVIEW | 4 | **1.000** | **1.000** | = 0.000 | +67.2 ms |
| SEMANTIC | 3 | 0.667 | 0.667 | = 0.000 | +62.9 ms |
| **Overall** | **16** | **0.812** | **0.688** | **▼ −0.125** | **+42.2 ms** |

> [!WARNING]
> The lexical layer **reduces** SYMBOL recall (−0.400). This is caused by BM25 scoring noise: for low-token queries like "Where is RETRIEVAL_ENABLE_LEXICAL configured?", the lexical results contaminate the top-10 with high-frequency token matches that displace the correct file-hint-injected results from the CONFIG routing path.

---

## Per-Case Detail

| ID | Family | Query | Dense hit | Lex hit | Lat dense | Lat lex |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| lex-sym-001 | SYMBOL | CODESEEK_DATABASE_URL | ✓ | ✓ | 448 ms | 335 ms |
| lex-sym-002 | SYMBOL | RETRIEVAL_ENABLE_LEXICAL configured | ✓ | ✗ | 316 ms | 336 ms |
| lex-sym-003 | SYMBOL | BAAI/bge-small-en-v1.5 configured | ✓ | ✗ | 640 ms | 562 ms |
| lex-sym-004 | SYMBOL | process_query defined | ✓ | ✓ | 33 ms | 92 ms |
| lex-sym-005 | SYMBOL | submission-key endpoint | ✓ | ✓ | 263 ms | 302 ms |
| lex-dep-001 | DEPENDENCY | qdrant-client declared | ✗ | ✗ | 253 ms | 293 ms |
| lex-dep-002 | DEPENDENCY | FastAPI dependency | ✗ | ✗ | 449 ms | 584 ms |
| lex-dep-003 | DEPENDENCY | invalidates lexical index | ✓ | ✓ | 37 ms | 95 ms |
| lex-dep-004 | DEPENDENCY | what calls run_query | ✓ | ✓ | 57 ms | 113 ms |
| lex-ov-001 | OVERVIEW | what is this project about | ✓ | ✓ | 113 ms | 169 ms |
| lex-ov-002 | OVERVIEW | what tech stack is used | ✓ | ✓ | 104 ms | 184 ms |
| lex-ov-003 | OVERVIEW | architecture overview | ✓ | ✓ | 237 ms | 309 ms |
| lex-ov-004 | OVERVIEW | what framework does this use | ✓ | ✓ | 95 ms | 156 ms |
| lex-sem-001 | SEMANTIC | auth session flow | ✓ | ✓ | 170 ms | 230 ms |
| lex-sem-002 | SEMANTIC | retrieval pipeline assembles context | ✗ | ✗ | 44 ms | 140 ms |
| lex-sem-003 | SEMANTIC | ingestion pipeline | ✓ | ✓ | 34 ms | 67 ms |

---

## Decision Gate

**Verdict: KEEP DISABLED — `RETRIEVAL_ENABLE_LEXICAL=False` is the correct default.**

| Gate condition | Threshold | Measured | Result |
| :--- | :---: | :---: | :---: |
| Mean latency delta | < 150 ms | +42.2 ms | ✓ acceptable |
| Any family Δhit | ≥ 0.02 | no family improved | ✗ no recall gain |

Both gates must pass to justify enabling. Latency is fine, but **recall does not improve** — and actively degrades for SYMBOL queries (−0.40). The RRF fusion layer does not yet apply per-family weighting, meaning lexical results pollute the SYMBOL path.

---

## Tuning Plan (per query family)

| Family | Δhit | Latency cost | Recommendation |
| :--- | :---: | :---: | :--- |
| SYMBOL | −0.400 | −14.6 ms (faster) | **Dense-only** — disable lexical for this intent path; BM25 pollutes CONFIG-injected file hints |
| DEPENDENCY | 0.000 | +72.8 ms | Equal — no gain; no reason to add latency |
| OVERVIEW | 0.000 | +67.2 ms | Equal — repo_summary injection already handles this path; lexical adds no signal |
| SEMANTIC | 0.000 | +62.9 ms | Equal — dense embedding already captures semantic similarity |

### When to revisit

Re-evaluate enabling the lexical layer after **at least one** of the following:

- [ ] Weighted per-family RRF is implemented so SYMBOL path can suppress lexical results
- [ ] The dependency-manifest DEPENDENCY cases (`qdrant-client`, `fastapi` in `pyproject.toml`) still hit@10=0 after other retrieval improvements — lexical may help there
- [ ] A new eval fixture with >20 cases per family establishes a stable baseline to detect smaller gains

### Defer weighted-fusion tuning

Per-family RRF weight tuning is **deferred** until:
1. The lexical layer can be selectively enabled per intent (gated by `primary_intent`)
2. The `retrieval_eval.py` suite is run with lexical enabled across all fixtures to establish per-family baselines

---

## Raw Results

See [`lexical_layer_benchmark_results.json`](./lexical_layer_benchmark_results.json) for the full per-case breakdown.
