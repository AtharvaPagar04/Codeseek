# CodeSeek Repository Freshness & Incremental Re-Indexing Report

This document outlines the current architecture of session management, repository loading, and indexing in CodeSeek, identifies gaps preventing real-time freshness tracking, and proposes a complete implementation plan for adding a feature to track and index the latest versions of repositories.

---

## 1. Current Implementation Map

The table below maps the exact files, functions, and classes responsible for the core workflows in CodeSeek.

| Workflow / Component | File Path | Function / Class / DDL |
| :--- | :--- | :--- |
| **Session Creation** | `frontend/src/utils/api.js`<br>`backend/retrieval/api_service.py`<br>`backend/retrieval/session_indexer.py` | `createSession`<br>`create_session_v1`<br>`create_session` |
| **Session List API** | `frontend/src/utils/api.js`<br>`backend/retrieval/api_service.py`<br>`backend/retrieval/session_indexer.py` | `listSessions`<br>`list_sessions_v1`<br>`list_sessions` |
| **Session Detail API** | `backend/retrieval/api_service.py`<br>`backend/retrieval/session_indexer.py` | `get_session_v1`<br>`get_session` |
| **Session Retry / Re-index API** | `frontend/src/utils/api.js`<br>`backend/retrieval/api_service.py`<br>`backend/retrieval/session_indexer.py` | `retrySessionIndexing`<br>`retry_session_v1`<br>`retry_indexing` |
| **Session DB Schema & Table** | `backend/retrieval/db.py` | `_BASE_SCHEMA_SQL` (table `repo_sessions`) |
| **Indexing Status Fields** | `backend/retrieval/session_indexer.py` | `status` (values: `indexing`, `ready`, `failed`), `error` |
| **Source Repo Location Storage** | `backend/retrieval/session_indexer.py` | `repo_root` column in DB (e.g., `WORKSPACE_ROOT / tenant_id / repo_slug`) |
| **GitHub Repo Clone/Update Logic** | `backend/retrieval/session_indexer.py` | `_clone_or_pull` (executes `git clone` or `git fetch` + `git pull`) |
| **Local Repo Indexing Logic** | `backend/rag_ingestion/main.py` | `run_pipeline` orchestration |
| **Incremental Skip Logic** | `backend/rag_ingestion/main.py`<br>`backend/rag_ingestion/utils/state.py` | `ENABLE_INCREMENTAL_FILE_SKIP`<br>`build_file_signature`, `is_file_unchanged` |
| **Deleted-File Cleanup Logic** | `backend/rag_ingestion/main.py` | Compares `previous_state` & `next_state`, deletes stale paths |
| **Qdrant Collection Naming** | `backend/retrieval/isolation.py` | `expected_collection_name` (SHA-256 hash of `repo_root` -> `repo_<hash>`) |
| **Qdrant Point IDs** | `backend/rag_ingestion/stages/metadata.py` | `build_metadata` (deterministic SHA-256 slice of path + symbol + part) |
| **Qdrant Payload Fields** | `backend/rag_ingestion/stages/storage.py` | `_payload` (stores metadata list and `content_excerpt`) |
| **Metadata Fields in Qdrant** | `backend/rag_ingestion/stages/storage.py` | `chunk_type`, `relative_path`, `symbol_name`, `labels`, `code_intent`, etc. |
| **Frontend Session Sidebar** | `frontend/src/components/Sidebar.jsx` | Renders `SessionItem` elements |
| **Frontend Session Detail View** | `frontend/src/components/SessionView.jsx` | Renders chat area, empty state, and status alerts |
| **Frontend Retry / Re-index Button** | `frontend/src/components/SessionView.jsx` | Rendered inside `StatusNotice` if session status is `'failed'` |
| **Indexing Live Logs** | `frontend/src/components/IndexingLiveLog.jsx` | Listens to SSE from `/api/v1/sessions/{session_id}/indexing-events/stream` |
| **Vector DB Audit Script** | `backend/scripts/manual_vector_db_audit.py` | Scroll validation script checking payloads and disk files |

---

## 2. Current Session Metadata

CodeSeek currently stores or can determine the following metadata:

* **Stored in DB (`repo_sessions`):**
  * `repo_url`: Repository clone URL.
  * `repo_root`: Full local path of the check-out workspace.
  * `collection`: Qdrant collection name.
  * `last_indexed_commit`: Stored as a string representing the Git HEAD commit SHA resolved at the time of indexing.
  * `chunks_generated` / `embeddings_stored`: Count of logical chunks and total stored points.
  * `enable_chunk_descriptions` / `refine_labels_with_llm`: Session flags.
* **Can be determined dynamically via Git:**
  * `repo_owner` / `repo_name`: Parsed from `repo_full_name` or `repo_url`.
  * `branch`: Can be checked using `git rev-parse --abbrev-ref HEAD` in the workspace directory.
  * `current_commit_sha`: Can be checked locally using `git rev-parse HEAD` or fetched remotely using `git ls-remote <repo_url> HEAD`.
  * `current_worktree_dirty`: Checked using `git status --porcelain`.
  * `last_indexed_at`: Represented by `job_finished_at`.

* **Not currently tracked or stored:**
  * `last_indexed_file_count`: Total files successfully parsed.
  * `last_indexed_chunk_count`: Redundant with `embeddings_stored` but helpful for visibility.
  * `current_branch`: Not explicitly cached (determined dynamically).

---

## 3. Current Qdrant Payload Metadata

Each point in Qdrant stores a payload containing standard structural, semantic, and syntax metadata.

* **Stored fields:** `chunk_id`, `file_path`, `relative_path`, `language`, `chunk_type`, `symbol_name`, `qualified_symbol`, `parent_symbol`, `signature` (for local language parse), `start_line`, `end_line`, `chunk_part`, `total_parts`, `token_count`, `summary`, `description`, `labels`, `code_intent`, and `content_excerpt`.
* **Not stored fields:** `session_id` (implied by unique collection mapping), `source/indexed commit SHA`, `indexed timestamp`.

### Specific Answers:
1. **Is there enough metadata to identify all points belonging to one file?**  
   Yes, the payload contains `relative_path`.
2. **Is there enough metadata to delete all Qdrant points for a deleted file?**  
   Yes. Points can be deleted using Qdrant’s field match filter: `Filter(must=[FieldCondition(key="relative_path", match=MatchAny(any=[path]))])`.
3. **Is there enough metadata to compare old chunks versus new chunks?**  
   No. Chunks themselves do not store individual commit SHAs or content hashes. Comparison is handled at the file level: if a file's fingerprint changes, all chunks for that path are deleted, and new ones are inserted.
4. **Are point IDs deterministic across indexing runs?**  
   Yes, they are SHA-256 hashes generated from the file's path, symbol name, parent symbol, and part index.
5. **If a file changes, do the old file’s points get deleted before new points are inserted?**  
   Yes, `delete_chunks_for_paths(modified_paths)` runs before `store_chunks`.
6. **If a file is deleted, are old points removed from Qdrant?**  
   Yes, at the end of `run_pipeline`, any path present in the previous state but missing in the next state is deleted.
7. **If a file is renamed, how would the current system behave?**  
   It is treated as a delete of the old file (stale chunks removed) and an addition of the new file (new chunks created).
8. **Does the audit script detect stale points?**  
   Yes. If `repo_root` is passed to `manual_vector_db_audit.py`, it verifies that each `relative_path` in Qdrant exists on disk; missing files trigger a validation failure.

---

## 4. Current Incremental / Delete Behavior

* **Recreating Collections:** Checked-out collections are NOT recreated by default. The environment variable `QDRANT_RECREATE_COLLECTION` defaults to `False`.
* **Incremental Flag:** `INGESTION_ENABLE_INCREMENTAL_FILE_SKIP` defaults to `True`.
* **File Fingerprint:** Built using `size_bytes` and `mtime_ns` (file modification time).
* **Storage Location:** Saved in `.rag_ingestion_state.json` inside the session's workspace root on disk.
* **Skip Behavior:** Unchanged files are completely skipped during parsing, chunking, and embedding.
* **Added Files:** Discovered and indexed.
* **Modified Files:** Deleted from Qdrant first, then re-indexed.
* **Deleted Files:** Deleted from Qdrant at the end of the pipeline execution.
* **Uncommitted Local Changes:** Since the pipeline runs inside the backend's cloned session workspace, size/mtime change, triggering incremental updates.
* **Remote Updates:** When a new commit is pulled, updated files change their file attributes (size or mtime), triggering incremental updates on those files during the next index run.

---

## 5. Gaps Preventing Repo Freshness Status

To support tracking whether an index is up to date, the following gaps must be addressed:
1. **Lightweight Remote Checking:** The backend has no mechanism to check if a remote repository has newer commits without fetching/cloning the whole branch.
2. **Missing Database Columns:** `repo_sessions` lacks tracking fields for the *current* state of the remote (e.g. `current_commit_sha`) and metadata about dirty states.
3. **No Freshness API:** There are no endpoints to query repository freshness or trigger a clean incremental re-index specifically.
4. **No UI Indicators:** The frontend sidebar and chat interface are unaware of commit discrepancies.

---

## 6. Proposed DB Fields

Add the following columns to the `repo_sessions` table in `backend/retrieval/db.py`:

```sql
ALTER TABLE repo_sessions ADD COLUMN current_commit_sha TEXT NOT NULL DEFAULT '';
ALTER TABLE repo_sessions ADD COLUMN current_branch TEXT NOT NULL DEFAULT '';
ALTER TABLE repo_sessions ADD COLUMN repo_dirty INTEGER NOT NULL DEFAULT 0;
ALTER TABLE repo_sessions ADD COLUMN repo_status_checked_at TEXT NOT NULL DEFAULT '';
ALTER TABLE repo_sessions ADD COLUMN files_indexed INTEGER NOT NULL DEFAULT 0;
```

---

## 7. Proposed API Endpoints

### 1. `GET /api/v1/sessions/{session_id}/repo-status`
Retrieves freshness info without performing a pull.

* **Response Schema (200 OK):**
```json
{
  "session_id": "8a9b1c...",
  "repo_status": {
    "status": "out_of_date", 
    "indexed_commit_sha": "abc12345",
    "current_commit_sha": "def67890",
    "indexed_at": "2026-06-08T04:00:00Z",
    "current_branch": "main",
    "dirty_worktree": false,
    "files_changed_count": 5
  }
}
```

### 2. `POST /api/v1/sessions/{session_id}/index-latest`
Triggers an index job targeting the latest commit.

* **Response Schema (200 OK):**
```json
{
  "session_id": "8a9b1c...",
  "status": "indexing",
  "message": "Indexing latest repository version."
}
```

---

## 8. Proposed Backend Indexing Changes

* **GitHub Freshness Check:**
  Use `git ls-remote <repo_url> HEAD` or query the GitHub `/commits` API using the user's stored OAuth token to get the latest SHA. Compare this to `last_indexed_commit`.
* **Local Freshness Check:**
  Run `git status --porcelain` and `git rev-parse HEAD` on the workspace directory.
* **"Index Latest" Flow:**
  1. Set session status to `indexing`.
  2. Fetch remote HEAD and pull changes (`git pull --ff-only`).
  3. Run the ingestion pipeline with `ENABLE_INCREMENTAL_FILE_SKIP=True`.
  4. Compare the files changed between `last_indexed_commit` and the new `HEAD` using `git diff --name-only <old_sha> <new_sha>`.
  5. Delete Qdrant points for files modified or deleted.
  6. Insert new points for added or modified files.
  7. Regenerate the repository summary chunk (`__repo_summary__.md`) to ensure the high-level overview remains accurate.
  8. Save the new `last_indexed_commit`, `chunks_generated`, and `embeddings_stored` fields in `repo_sessions`.

---

## 9. Proposed Frontend UI Changes

1. **Sidebar Freshness Badge:**
   In `SessionItem.jsx`, render a small dot or pill next to the status badge:
   * **Up to date:** Green dot/badge.
   * **Out of date:** Amber badge ("Out of Date").
   * **Dirty:** Orange warning ("Uncommitted").
2. **Options Menu Addition:**
   Add a button in the `SessionItem` 3-dot dropdown menu:
   * **"Index Latest Version"** (disabled if status is already `indexing`).
3. **Session View Freshness Banner:**
   If the session is loaded and out of date, show a prominent notice bar at the top of the chat area:
   * *"This index represents commit abc12345. A newer commit def67890 is available on GitHub. [Index Latest Version]"*

---

## 10. Recommended V1 Implementation Plan (Safe Re-index)

* **Design Strategy:**
  For V1, when a user clicks "Index Latest Version", the system should pull the repository changes, clear the session's existing Qdrant collection, and perform a full indexing run. This is the safest approach to prevent stale embeddings and ensure absolute data consistency.
* **Workflow:**
  1. Trigger indexing using `POST /sessions/{id}/index-latest`.
  2. In the background thread, execute `git pull`.
  3. Re-create the Qdrant collection (`QDRANT_RECREATE_COLLECTION=True`).
  4. Index the workspace from scratch.
  5. Update the DB metadata.

---

## 11. Recommended V2 Improvements (Incremental Updates)

* **Design Strategy:**
  Optimize re-indexing by performing incremental updates inside Qdrant instead of rebuilding the entire collection.
* **Workflow:**
  1. Detect modified, added, and deleted files using `git diff --name-only <old_commit> <new_commit>`.
  2. Run the ingestion pipeline, passing the diff file list.
  3. Delete Qdrant points only for modified and deleted files.
  4. Parse and generate embeddings only for the changed files.
  5. Append new embeddings to the existing collection.

---

## 12. Risks and Edge Cases

| Risk / Edge Case | Impact | Recommended V1 Handling |
| :--- | :--- | :--- |
| **GitHub repo deleted / private** | `git fetch` fails. | Catch the exception, mark the session status as `failed` with error message, and preserve the old database metadata. |
| **Local branch changed** | Pulling fails or indexes wrong branch. | Force checkout of the expected branch before indexing. |
| **Force push / history rewrite** | `git pull --ff-only` fails. | Catch error, fall back to deleting the workspace directory and performing a clean clone + full index. |
| **Large repository re-indexing** | High VRAM or timeout. | Run indexing in a background worker thread. Disable UI chat inputs during index runs. |
| **Qdrant delete fails** | Stale points left in DB. | Run deletes inside try/except blocks; if failure occurs, mark status as `failed`. |
| **User triggers LLM label refinement during re-indexing** | Discrepancies in labels. | Cache active configuration at the start of the job and apply consistently. |

---

## 13. Exact Files to Modify

* **Backend DB Schema:**
  `backend/retrieval/db.py` (update DDL schema and add migrations).
* **Backend Indexer Orchestration:**
  `backend/retrieval/session_indexer.py` (add freshness check utilities, index-latest job thread).
* **Backend API Routes:**
  `backend/retrieval/api_service.py` (add endpoints `/sessions/{id}/repo-status` and `/sessions/{id}/index-latest`).
* **Frontend API Utilities:**
  `frontend/src/utils/api.js` (add client functions `fetchRepoStatus`, `indexLatestVersion`).
* **Frontend Components:**
  * `frontend/src/components/SessionItem.jsx` (add options menu button & freshness badge).
  * `frontend/src/components/SessionView.jsx` (add freshness warning banner).

---

## 14. Tests to Add

* **Backend Tests (`backend/tests/`):**
  * `test_git_metadata_fetching`: Mocks Git command outputs and verifies SHA resolution.
  * `test_repo_status_endpoint`: Tests GET endpoint outputs under various mock DB states.
  * `test_index_latest_success`: Tests successful re-indexing triggers.
* **Frontend Tests (`frontend/src/utils/api.test.js`):**
  * Mock API endpoints and verify UI state updates upon receiving freshness statuses.

---

## 15. Manual Validation Plan

1. **Verify Freshness Check:**
   * Create a session for a test repository.
   * Push a new commit to the remote repository.
   * Reload the CodeSeek dashboard and verify the session shows the **"Out of Date"** badge.
2. **Verify Index Latest Option:**
   * Click the **"Index Latest Version"** option in the 3-dot dropdown.
   * Confirm the status changes to **"Indexing"** and the live log overlay is displayed.
   * Check Qdrant points and verify the new commit's files are represented.
3. **Verify Error Handling:**
   * Revoke GitHub credentials or make the test repository private.
   * Attempt re-indexing and verify the session correctly transitions to **"Failed"** without destroying the old index.
