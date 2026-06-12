# CodeSeek Release & Demo Readiness Checklist V1

This checklist verifies all core capabilities and user-facing surfaces of CodeSeek to ensure everything is ready for production deployment or a live product demonstration.

---

## Checklist Summary

### 1. Core Retrieval Readiness
- [ ] Swappable LLM Providers (OpenAI, Gemini, Groq, OpenRouter) function correctly.
- [ ] Evidences are extracted from both code ASTs and overview documentation files (Markdown, JSON, YAML).
- [ ] Intent classification routes query modes (`code_snippet`, `overview`, `explanation`) correctly.

### 2. Repository Freshness Readiness
- [ ] Freshness calculation detects `dirty_worktree` when there are unstaged changes.
- [ ] Freshness calculation detects `untracked_files` when new files are added.
- [ ] Git commit mismatch triggers freshness status changes.
- [ ] Freshness API endpoint returns accurate status fields.
- [ ] UI shows appropriate visual warnings when status is not `fresh`.

### 3. Index Latest Readiness
- [ ] Full indexing cloned/re-indexed from the latest state is supported.
- [ ] SQLite/Postgres schemas contain all updated columns (`last_indexed_commit`, etc.).
- [ ] Full indexing status updates progress dynamically in the UI.

### 4. Incremental Indexing Readiness
- [ ] Feature flag `CODESEEK_ENABLE_INCREMENTAL_REINDEX=true` enables incremental reindexing.
- [ ] Change detection finds added, modified, and deleted files.
- [ ] Incremental preview panel displays the list of files to update.
- [ ] "Index changed files" executes a partial run that only processes modified files.

### 5. Background Job Reliability Readiness
- [ ] Background indexing runs asynchronously on thread pools.
- [ ] Database contains the `indexing_jobs` table for state tracking.
- [ ] Job status progresses correctly: `queued` → `indexing` → `succeeded` / `failed`.
- [ ] Metric counts (`files_indexed`, `chunks_generated`, `embeddings_stored`) are updated.

### 6. Cancellation and History Readiness
- [ ] Cooperative cancellation checks between stages and file batches.
- [ ] "Cancel" button in UI stops the job, updating status to `cancelled`.
- [ ] Recent indexing jobs table displays the job history (up to 20 jobs).

### 7. Session Cleanup Readiness
- [ ] Deleting a repository session requires user confirmation in the UI.
- [ ] Active indexing sessions cannot be deleted (rejects with a warning).
- [ ] Session deletion drops associated DB rows across all tables and drops the Qdrant collection safely.

### 8. Multi-Branch Awareness Readiness
- [ ] System tracks the `indexed_branch` and `current_branch`.
- [ ] Switching git branches triggers `branch_changed` state.
- [ ] UI alerts user to branch mismatch and blocks incremental reindexing if unsafe.

### 9. Diagnostics and Source-Card Readiness
- [ ] Diagnostics panel displays sub-stage timings, intents, and token counts.
- [ ] "Copy Diagnostics" copies clean, structured text output.
- [ ] Source cards show correct syntax highlighting, line numbers, and paths.

### 10. Evaluation Dashboard Readiness
- [ ] Evaluation UI panel shows calibration runs and RAGAS metrics.
- [ ] Human review flags are recorded and rendered correctly.

### 11. Security/Secrets Readiness
- [ ] Credentials (tokens, connection strings) in error logs, warnings, and job details are redacted.
- [ ] API exceptions and validation details do not expose credentials.
- [ ] Copy diagnostics output and UI rows do not leak sensitive prompts or keys.

### 12. Demo Readiness
- [ ] Quick check script `scripts/demo_local.sh` runs successfully.
- [ ] README.md contains full demo guidelines and instructions.

### 13. Performance Baseline Readiness
- [ ] Performance script `scripts/perf_baseline.sh` executes successfully.
- [ ] Guidelines in `docs/product/performance_baseline.md` are documented.

---

## 14. Known Feature Flags

- **`CODESEEK_ENABLE_INCREMENTAL_REINDEX=true`**: Required to unlock the incremental file reindexing capabilities and its preview panel in the UI.

---

## 15. Pre-Demo Focused Validation Commands

Run these fast validation commands before starting a demo to verify system integrity:

```bash
# 1. Dependency and Infra Check
./scripts/demo_local.sh --check-only

# 2. Performance Baseline Sanity
./scripts/perf_baseline.sh --dry-run --run-query --run-index

# 3. Backend focused test checks
PYTHONPATH=backend backend/.venv/bin/pytest \
  backend/tests/test_security_sanitization.py \
  backend/tests/test_session_cleanup.py \
  backend/tests/test_indexing_jobs.py

# 4. Frontend focused test checks
cd frontend && node --test src/components/answerDiagnostics.test.js
```

---

## 16. Known Limitations

1. **Local Embeddings Load**: First-time index runs download the local embedding model, taking several minutes depending on network speeds.
2. **Git Branch Mismatch**: Incremental indexing is disabled when there is a git branch mismatch between the working tree and the indexed state. A full reindex is required to align them.
3. **Database Locks**: Under highly concurrent sqlite runs, transient database busy locks might occur; the system retries queries automatically.

---

## 17. Recommended Demo Script

Follow the step-by-step presentation script located at [`docs/product/demo_script.md`](demo_script.md) for a seamless 10-minute presentation highlighting:
1. Repository session creation and clone.
2. Background indexing job visualization and cancellation.
3. Natural language query resolution, source citations, and diagnostics.
4. Repo freshness changes (creating dirty files, branch changes) and incremental re-indexing.
5. Session cleanup confirmation.
