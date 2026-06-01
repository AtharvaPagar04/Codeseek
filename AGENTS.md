# Codeseek Development Context

This project is a local-only Python RAG ingestion pipeline for code repositories.
It ingests local folders or public GitHub repositories, parses source files into
AST-aware chunks, generates embeddings, and stores metadata/vectors in Qdrant.

Authoritative docs:
- `docs/architecture.md`
- `docs/ingestion_pipeline_docs.md`

Execution roadmap:
- `docs/implementation_roadmap.md`

When docs disagree, follow this order:
1. `docs/architecture.md`
2. `docs/ingestion_pipeline_docs.md`
3. `docs/implementation_roadmap.md`
4. `docs/tasks.md`

Implementation rules:
- Keep the architecture modular.
- Use the `rag_ingestion/` package structure from `docs/architecture.md`.
- Stage files must not import from other stage files.
- Dataclasses live in `rag_ingestion/models/`.
- Shared counters and logging live in `rag_ingestion/utils/`.
- Keep `main.py` as orchestration only.
- No retrieval, chat, agents, auth, web UI, or production hardening unless asked.
- Prefer local deterministic logic. Summary generation must not use an LLM.
- Qdrant is local on port `6333`.

Development style:
- Implement one roadmap phase at a time.
- Validate the current phase before moving to the next phase.
- Validation must include at least a file layout check and a lightweight syntax/test
  check appropriate for the phase.
- Do not start the next phase if validation fails; fix the current phase first.
- Add focused tests for dataclasses and pure stages first.
- Keep dependencies explicit in `requirements.txt`.
- Do not store chunk content or embeddings in Qdrant payload.
- Do not introduce unrelated framework choices.
