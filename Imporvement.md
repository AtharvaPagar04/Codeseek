Here's a thorough analysis of your RAG system, organized by where improvements will have the most impact:

---

## Summary

Your system is a well-structured, defensively designed code-retrieval pipeline. It's strong on grounded symbol lookups but weak on broad repository understanding. The root cause is almost always **missing evidence upstream**, not bad prompting downstream.

---

## Improvement Areas, Ranked by Impact

### 🔴 Critical — Fix These First

**1. Ingest non-code files (Biggest single win)**
Your ingestion only handles `.py`, `.js`, `.ts`, `.tsx`, `.jsx`. Files like `README.md`, `package.json`, `requirements.txt`, `docker-compose.yml`, `pyproject.toml`, `vite.config.*`, and YAML/TOML configs are completely skipped. Yet your *retrieval layer already knows these matter* — `_overview_priority()` specifically ranks them highly. The ranking is smarter than the corpus. Fix ingestion first; treat these as structured file-summary chunks with parsed metadata and raw excerpt content.

**2. Add sparse/lexical retrieval (BM25)**
Right now, your only semantic layer is `BAAI/bge-small-en-v1.5` (384-dim dense vectors). There's no BM25 or fuzzy matching. This means:
- exact symbol names with slight variations miss
- text-heavy questions over docs/configs are weak
- dense retrieval is doing all the work alone

Add BM25 over `file content + chunk summaries + paths + symbol names` and fuse it with dense retrieval using RRF or a weighted merge.

**3. Pre-generate a repo-summary chunk during ingestion**
Overview answers are currently derived at query time from whatever chunks happen to be retrieved. Instead, generate a stable, high-priority `repo_summary` chunk at ingestion time covering: purpose, entrypoints, frameworks, key services, and config files. This gives overview queries a reliable, always-present retrieval target.

---

### 🟠 High Impact — Address Soon

**4. Query understanding: replace regex with lightweight NLU**
Intent classification (`SEMANTIC` / `DEPENDENCY` / `SYMBOL`) and entity extraction are entirely regex-based. Failure modes are predictable:
- broad semantic questions get misclassified as symbol lookups
- file detection misses non-code extensions
- dependency phrasing ("leverages", "invokes") is unrecognized
- follow-up rewriting is shallow concatenation, not discourse understanding

Consider a small classifier (even a fine-tuned embedding + logistic layer) or a structured LLM call for intent + entity extraction on the query itself before retrieval.

**5. Expand import and dependency tracing**
Import-backed candidate injection currently handles only named JS/TS imports with `@/` aliases. It completely misses:
- Python `import x from y` patterns
- default imports
- namespace imports (`import * as X`)
- re-export chains
- JSON/YAML/config imports
- backend route → service → DB dependency chains

This limits the system's ability to follow the actual data/call flow across a repo.

**6. Widen the answer gate: two-layer source model**
The current strict allowed-source list reduces hallucination but also hurts answer quality when a useful chunk was filtered. Consider splitting:
- `display_sources` — tight list for user-facing citations
- `reasoning_sources` — broader list the LLM can use to synthesize (but not cite directly)

This preserves grounding while giving the model more evidence to work with.

---

### 🟡 Medium Impact — Worth Addressing

**7. Improve follow-up query rewriting**
Current follow-up resolution prepends the previous raw query when it detects shallow markers (`also`, `it`, `that`, `more`, etc.). This is brittle. Improve by:
- storing previously cited symbols and files in memory
- resolving pronouns against recent entities explicitly
- using a small rewrite model or structured LLM call for discourse coreference

**8. Generalize the deterministic explanation builder**
`explanation mode` is well-tuned for frontend components with named exported data arrays but falls apart for:
- backend orchestration flows
- infra/config explanations
- multi-file service traces

If explanation mode is bypassing the LLM, it needs to be general enough to handle those shapes.

**9. Implement sibling expansion**
The config flag for sibling expansion exists but is marked as not implemented. Sibling chunks (methods in the same class) are frequently relevant when a user asks about a class or module, and their absence can truncate useful context.

---

### 🟢 Lower Priority — Do Last

**10. Upgrade the embedding model**
`BAAI/bge-small-en-v1.5` at 384 dimensions is fast but limited. Once ingestion and retrieval structure are solid, upgrading to `bge-base` (768-dim) or a code-tuned model like `nomic-embed-code` will improve semantic recall, especially for cross-language repos.

**11. Add evaluation sets for broad questions**
The system lacks quality evals for the queries it handles worst:
- "What is this project about?"
- "What's the tech stack?"
- "How does auth work end to end?"

Build eval sets across repo shapes (frontend-only, backend-only, monorepo, infra-heavy) before doing further prompt tuning — otherwise you're optimizing blind.

**12. Revisit prompt tuning — last**
The system prompt and user prompt construction are already reasonably well-structured. Prompt tuning should come *after* fixing ingestion and retrieval, not before. The current overview failures are caused by missing evidence, not missing instructions.

---

## Quick Reference Priority Table

| Priority | Area | Expected Impact |
|---|---|---|
| 🔴 1 | Ingest README/config/YAML/TOML | Fixes overview entirely |
| 🔴 2 | Add BM25 sparse retrieval | Fixes exact-match + docs queries |
| 🔴 3 | Pre-generate repo-summary chunk | Stable target for overview queries |
| 🟠 4 | Better query intent/entity understanding | Reduces misrouted queries |
| 🟠 5 | Expand import tracing (Python, re-exports) | Better cross-file answers |
| 🟠 6 | Two-layer source gating | Wider synthesis, same citation safety |
| 🟡 7 | Better follow-up rewriting | Fewer broken multi-turn sessions |
| 🟡 8 | Generalize explanation mode | Fixes backend/infra explanations |
| 🟡 9 | Implement sibling expansion | Fills class-level context gaps |
| 🟢 10 | Upgrade embedding model | Marginal recall improvement |
| 🟢 11 | Add broad-question eval sets | Better optimization signal |
| 🟢 12 | Prompt tuning | Diminishing returns until above are done |