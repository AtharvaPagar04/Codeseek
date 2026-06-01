# Codeseek Tasks

Use `docs/ingestion_docs/implementation_roadmap.md` as the active task sequence.
The files `docs/ingestion_docs/architecture.md` and `docs/ingestion_docs/ingestion_pipeline_docs.md` remain the
principal design documents.

## Current Phase: Retrieval Integration

- Core roadmap phases (1-6) are implemented.
- Private GitHub clone token support is implemented (`GITHUB_TOKEN` / `GH_TOKEN`).
- Incremental unchanged-file skip is implemented with local state persistence.
- Incremental stale-point cleanup is implemented for files removed from source repo.
- Retrieval pipeline is implemented under `retrieval/` and validated against local Qdrant.
- Retrieval LLM provider is Groq (`GROQ_API_KEY`).

## Active Work Queue

- Keep docs and counters aligned with code behavior.
- Continue retrieval quality tuning against hard eval suites.
- Operationalize production rollout (monitoring dashboards, backup restore drills, staged deploys).

## Implementation Prompt

Use this from `/home/arch/DEV/RAG/Codeseek`:

```text
Read AGENTS.md, docs/ingestion_docs/architecture.md, docs/ingestion_docs/ingestion_pipeline_docs.md,
docs/ingestion_docs/implementation_roadmap.md, and docs/ingestion_docs/tasks.md. Implement the current
active task queue item only.
```
