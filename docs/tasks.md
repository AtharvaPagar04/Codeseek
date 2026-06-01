# Codeseek Tasks

Use `docs/implementation_roadmap.md` as the active task sequence.
The files `docs/architecture.md` and `docs/ingestion_pipeline_docs.md` remain the
principal design documents.

## Current Phase: Production Hardening (Post-Phase 6)

- Core roadmap phases (1-6) are implemented.
- Private GitHub clone token support is implemented (`GITHUB_TOKEN` / `GH_TOKEN`).
- Incremental unchanged-file skip is implemented with local state persistence.

## Active Work Queue

- Implement stale-point cleanup in Qdrant for files deleted from source repo during incremental mode.
- Optionally strengthen file change detection using content hash in addition to `size_bytes` + `mtime_ns`.
- Keep docs and counters aligned with code behavior.

## Implementation Prompt

Use this from `/home/arch/DEV/RAG/Codeseek`:

```text
Read AGENTS.md, docs/architecture.md, docs/ingestion_pipeline_docs.md,
docs/implementation_roadmap.md, and docs/tasks.md. Implement the current
active task queue item only.
```
