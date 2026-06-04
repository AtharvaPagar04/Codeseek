# Deployment Readiness Checklist

Use this document as the single deployment checklist for Codeseek.

> **Legend**
> - `[x]` Implemented in code — verify it works in your environment.
> - `[ ]` Operator action required — must be done as part of your deployment.

## 1. Infrastructure

- [ ] Provision the deployment environment for frontend, backend, Postgres, and Qdrant.
- [x] Verify Docker / container runtime is available in the target environment. *(docker-compose.yml provided)*
- [x] Configure persistent storage for Postgres. *(named volume `postgres_data` in docker-compose.yml)*
- [x] Configure persistent storage for Qdrant. *(named volume `qdrant_storage` in docker-compose.yml)*
- [x] Ensure backend repo workspace storage is available and writable. *(named volume `repo_workspace` + `CODESEEK_REPO_WORKSPACE` env in docker-compose.yml)*
- [ ] Confirm required ports and internal network access are available.
- [x] Configure process supervision for backend services. *(`restart: unless-stopped` on all services)*
- [x] Verify service restart behavior after host/container restart. *(`restart: unless-stopped` policy)*

## 2. Environment Configuration

- [ ] Set `CODESEEK_API_KEY`.
- [ ] Set `CODESEEK_APP_ENCRYPTION_KEY`.
- [ ] Set `CODESEEK_DB_BACKEND=postgres`.
- [ ] Set `CODESEEK_DATABASE_URL`.
- [ ] Set `CODESEEK_CORS_ORIGINS` for the deployed frontend origin.
- [ ] Set `CODESEEK_AUTH_SESSION_SECURE_COOKIE=1`.
- [ ] Set `CODESEEK_ENFORCE_HTTPS=1`.
- [ ] Set `CODESEEK_ALLOW_PLAINTEXT_SECRET_SUBMISSION=0`.
- [ ] Set `CODESEEK_REQUIRE_EXPLICIT_APP_ENCRYPTION_KEY=1`.
- [ ] Set `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` from your GitHub OAuth App.
- [ ] Set `GITHUB_REDIRECT_URI` to the **backend** callback URL (e.g. `https://api.your-domain.com/auth/github/callback`). ⚠️ This must point to the backend, NOT the frontend — GitHub OAuth is now backend-initiated. Update the Authorization callback URL in your GitHub OAuth App settings to match.
- [ ] Set `CODESEEK_FRONTEND_URL` to the deployed frontend origin (e.g. `https://your-domain.com`) so the backend can redirect the popup back correctly.
- [ ] Verify the GitHub OAuth App's "Authorization callback URL" in GitHub settings matches `GITHUB_REDIRECT_URI` exactly.
- [ ] Confirm no local-development placeholder values remain in `.env`. *(templates now provided in `backend/.env.example`, `frontend/.env.production.example`, and `deploy/.env.example`)*
- [ ] Confirm secrets are injected through the deployment platform, not committed files.

## 3. Security

- [ ] Put the app behind HTTPS/TLS. *(production reverse-proxy scaffold now provided via `docker-compose.deploy.yml` + `deploy/Caddyfile`)*
- [x] Confirm TLS termination forwards `X-Forwarded-Proto: https` when needed. *(`CODESEEK_TRUST_X_FORWARDED_PROTO` flag + `enforce_https_middleware` in api_service.py)*
- [x] Verify secure auth cookies are set in deployment. *(`AUTH_SESSION_SECURE_COOKIE` flag wires into all `set_cookie` calls)*
- [x] Verify plaintext secret submission is disabled in deployment. *(`ALLOW_PLAINTEXT_SECRET_SUBMISSION=0` + enforcement in `_resolve_submitted_secret`)*
- [x] Verify secret-bearing request bodies are not logged. *(`sanitize_for_log` in observability.py redacts token/key/secret/cookie/ciphertext fields)*
- [ ] Review backend logs for accidental token, key, cookie, or ciphertext exposure.
- [x] Confirm GitHub access tokens are only stored server-side in encrypted form. *(`encrypt_secret` in github_store.py via crypto_store)*
- [x] Confirm provider API keys are only stored server-side in encrypted form. *(`encrypt_secret` in provider_store.py)*
- [x] Review API endpoints for missing auth checks. *(all `/api/v1/*` endpoints call `_require_auth`; session/thread/provider endpoints also call `_require_auth_user`)*
- [x] Review API endpoints for missing ownership checks. *(`_session_visible_to_user` / `_thread_visible_to_user` enforced on every read and write)*
- [x] Verify per-user session isolation. *(user_id scoped in `_find_existing_session` and `list_sessions_v1`)*
- [x] Verify per-user provider credential isolation. *(all provider_store queries are scoped to `user_id`)*
- [x] Verify per-user GitHub credential isolation. *(github_store keyed by `user_id` with `UNIQUE` constraint)*
- [x] Verify per-user chat/thread isolation. *(`_thread_visible_to_user` guard on all thread/message endpoints)*

## 4. Database and Persistence

- [ ] Start the backend successfully with Postgres. *(production compose path now provided via `docker-compose.deploy.yml`)*
- [x] Verify tables are created in Postgres. *(`_init_postgres` in db.py runs the full schema on startup)*
- [x] Verify no SQLite file is used in Postgres mode. *(`validate_postgres_readiness.py` asserts `no_sqlite_file_used`)*
- [x] Verify GitHub login creates a `users` row. *(`upsert_github_user` in auth_store.py)*
- [x] Verify provider credential add creates a `user_provider_credentials` row. *(`create_provider_credential` in provider_store.py)*
- [x] Verify session creation creates a `repo_sessions` row. *(`create_session` in session_indexer.py)*
- [x] Verify chat usage creates `chat_threads`, `chat_messages`, and `thread_memory` rows. *(`append_thread_message` + `save_thread_memory` in chat_store/memory_store)*
- [x] Verify app restart preserves users, sessions, chats, and credentials. *(`validate_postgres_readiness.py` tests `restart_preserves_state`)*
- [x] Verify duplicate session creation for the same user/repo returns the existing session. *(`_find_existing_session` in session_indexer.py + `test_create_session_reuses_existing_repo_session`)*
- [x] Verify session deletion behaves correctly. *(`delete_session_v1` + `test_delete_and_retry_helpers`)*
- [x] Verify retry indexing behaves correctly. *(`retry_indexing` in session_indexer.py + `test_delete_and_retry_helpers`)*

## 5. Qdrant and Indexing

- [ ] Verify Qdrant is reachable from the backend. *(production compose path now provided via `docker-compose.deploy.yml`)*
- [x] Verify repo session creation triggers clone/pull and indexing. *(`_enqueue_index_job` → `_index_job` in session_indexer.py)*
- [x] Verify indexing transitions from `indexing` to `ready`. *(`_update_session(status="ready")` after successful `run_pipeline`)*
- [x] Verify indexing failures transition to `failed` with usable error messages. *(`_update_session(status="failed", error=str(exc))` in `_index_job`)*
- [x] Verify the selected repo at session creation is the repo that gets indexed. *(collection and repo_root are set from `create_session` parameters)*
- [x] Verify duplicate sessions are not created for the same repo/user. *(`_find_existing_session` deduplication)*
- [x] Verify repo workspace reuse behaves correctly across repeated indexing. *(`_clone_or_pull` does `git fetch + pull` when `.git` already exists)*
- [x] Verify indexed collections remain isolated per repo. *(`expected_collection_name` derives collection name from repo path; `validate_collection_binding` enforces it)*
- [x] Verify collection naming matches the repo/session isolation model. *(`isolation.py` + `test_isolation_policy.py`)*

## 6. Authentication and GitHub Integration

- [ ] Decide whether deployment will use GitHub OAuth, PAT connect, or both. *(see runbook section 2 "GitHub Auth Mode")*
- [x] Verify GitHub OAuth configuration end to end. *(`/auth/github/login` → `/auth/github/callback` popup flow implemented; `test_api_service_github_auth.py`)*
- [x] Verify PAT connect configuration end to end. *(`POST /auth/github/token` accepts encrypted PAT)*
- [ ] Verify GitHub session login works from the frontend.
- [x] Verify GitHub repo listing works for the authenticated user. *(repo picker auto-loads repositories after auth; E2E coverage added in `tests/e2e/specs/01_github_connect.spec.js`)*
- [x] Verify GitHub auth failure states are clear in the UI. *(`_oauth_popup_html(success=False, error=...)` renders error in popup)*
- [x] Verify expired auth-session handling and re-login flow. *(`get_user_for_session_token` checks `expires_at`; expired sessions return `null` → frontend shows login)*
- [x] Verify logout clears the auth session correctly. *(`delete_auth_session` in `auth_logout`)*

## 7. Provider Credential Flow

- [x] Verify provider submission key endpoint works. *(`GET /api/v1/crypto/submission-key` returns RSA public key + key_id)*
- [x] Verify encrypted provider credential submission works from the frontend. *(`_resolve_submitted_secret` → `decrypt_submission_secret`; `test_api_service_encrypted_submission.py`)*
- [x] Verify provider credentials list correctly after save. *(`list_provider_credentials` excludes `api_key` from list response)*
- [x] Verify provider credential activation works. *(`activate_provider_credential_v1` → `set_active_provider_credential`)*
- [x] Verify provider credential deletion works. *(`delete_provider_credential_v1` + `_ensure_one_active`)*
- [x] Verify provider credentials survive browser refresh. *(UI modal now reloads persisted credentials after refresh; E2E coverage added in `tests/e2e/specs/02_provider_credential.spec.js`)*
- [x] Verify provider credentials survive backend restart. *(`validate_postgres_readiness.py` confirms persistence)*
- [x] Verify missing provider credential state is clear in the UI. *(query returns `400 "No active provider credential"` which frontend handles)*
- [x] Verify invalid provider configuration state is clear in the UI. *(backend now surfaces provider auth/config failures as structured errors; frontend maps them to actionable retry/update copy in `src/utils/api.js`)*
- [x] Verify provider rate-limit (`429`) state is clear in the UI. *(provider `429` now propagates through the API and frontend shows retry/switch-provider guidance)*

## 8. Query and Chat Flow

- [ ] Verify query roundtrip works from the frontend.
- [x] Verify chat history persists after refresh. *(`list_thread_messages` reads from DB; messages written on every query)*
- [x] Verify chat history persists after backend restart. *(`validate_postgres_readiness.py` checks message persistence)*
- [x] Verify thread memory persists correctly. *(`save_thread_memory` / `get_thread_memory` in memory_store; `validate_postgres_readiness.py`)*
- [x] Verify hidden thread behavior does not leak or clear the wrong messages. *(`clear_thread_messages` scoped by `thread_id`; `_thread_visible_to_user` guards read access)*
- [ ] Verify low-context fallback responses are acceptable.
- [ ] Verify overview queries produce acceptable answers.
- [ ] Verify tech-stack queries produce acceptable answers.
- [ ] Verify explanation-mode queries produce acceptable answers.
- [ ] Verify section queries that depend on imported data produce acceptable answers.

## 9. UI and UX

- [x] Verify indexing progress is visible and understandable. *(sidebar status badges + session status notices for `indexing` / `ready` / `failed`)*
- [x] Verify indexing failure UI provides actionable retry guidance. *(failed session notice now includes a `Retry indexing` action wired to `POST /api/v1/sessions/{id}/retry`)*
- [x] Verify expired auth state is visible and recoverable. *(`useGitHub` now surfaces session-expiry copy in the header; reconnect flow remains available from `StatusBar`)*
- [x] Verify missing provider configuration guidance is visible and actionable. *(`ApiTokensModal` empty state now explains provider-key setup; query errors still direct users back to API Config)*
- [x] Verify session list behavior is correct after creation, deletion, and reuse. *(`normalizeSessionRecord` preserves local thread state when a backend session is reused; create/delete/retry notices added in `App.jsx`)*
- [x] Verify duplicate repo selection returns the existing session cleanly in the UI. *(repo re-selection now reopens the existing session and shows an explicit reuse notice instead of rebuilding empty local state)*
- [x] Verify mobile layout is usable for session and chat flows. *(Playwright coverage added in `tests/e2e/specs/07_ui_validation.spec.js` for compact viewport shell behavior)*
- [x] Verify source rendering is readable. *(response UI still renders source cards; source-copy behavior improved to include file, symbol, and line data)*
- [ ] Verify source copy / follow-up behavior is acceptable for deployment.

## 10. Observability and Operations

- [x] Verify `/api/v1/health` works in deployment. *(`health_v1` endpoint + `_health_payload` checks Qdrant + startup errors)*
- [x] Verify `/api/v1/metrics` works in deployment. *(`metrics_v1` returns Prometheus text format via `prometheus_client`)*
- [x] Add monitoring for backend health. *(`monitoring/prometheus.yml` scrapes `/api/v1/metrics`; `docker-compose.monitoring.yml` runs Prometheus)*
- [x] Add monitoring for Postgres health. *(`pg_exporter` service in `docker-compose.monitoring.yml` + `PostgresDown` / `PostgresConnectionsHigh` alerts)*
- [x] Add monitoring for Qdrant health. *(`monitoring/prometheus.yml` scrapes Qdrant `/metrics`; `QdrantDown` alert in `monitoring/alerts.yml`)*
- [x] Add alerting for backend startup failure. *(`BackendDown` + `BackendStartupErrors` rules in `monitoring/alerts.yml`)*
- [x] Add alerting for indexing failures. *(`IndexingFailures` alert rule in `monitoring/alerts.yml`)*
- [x] Add alerting for repeated provider failures. *(`ProviderRateLimitSpike` alert rule in `monitoring/alerts.yml`)*
- [x] Add alerting for repeated auth failures. *(`AuthFailureSpike` alert rule in `monitoring/alerts.yml`)*
- [x] Add alerting for rate-limit spikes. *(`ProviderRateLimitSpike` alert + `HighQueryLatency` rule in `monitoring/alerts.yml`)*
- [x] Review structured logs for deployment usefulness. *(`log_event` emits JSON with `ts_ms`, `event`, `request_id`; all sensitive fields redacted by `sanitize_for_log`)*

## 11. Backups and Recovery

- [x] Verify Postgres backup procedure works. *(`scripts/smoke_test_postgres_backup.py` — runs `pg_dump` against a temp DB and verifies the dump is non-empty)*
- [x] Verify Postgres restore procedure works. *(`scripts/smoke_test_postgres_backup.py` — restores dump via `psql` into a second temp DB and verifies sentinel rows)*
- [x] Verify Qdrant snapshot backup procedure works. *(`scripts/qdrant_snapshot_backup.py` — creates snapshot and downloads it)*
- [x] Verify Qdrant snapshot restore procedure works. *(`scripts/qdrant_snapshot_restore.py` — uploads snapshot to Qdrant)*
- [x] Define backup schedule. *(see runbook section 7 — daily Postgres, daily Qdrant via `qdrant_snapshot_schedule.py`)*
- [x] Define retention policy for backups. *(see runbook section 7 — 7 daily + 4 weekly Postgres; 14 snapshots / 30 days Qdrant)*
- [x] Test recovery from backup in a non-production environment. *(`scripts/smoke_test_postgres_backup.py` — creates/drops isolated temp databases; safe to run against production Postgres)*

## 12. Cleanup and Retention

- [x] Define retention policy for stale repo sessions. *(operator-defined; `cleanup_stale_workspaces.py --max-age-days`)*
- [x] Define retention policy for old repo workspaces. *(`scripts/cleanup_stale_workspaces.py` — removes orphaned workspace dirs older than threshold)*
- [x] Define retention policy for expired auth sessions. *(`AUTH_SESSION_TTL_SECONDS` env var; default 30 days)*
- [x] Add cleanup jobs for stale repo workspaces. *(`scripts/cleanup_stale_workspaces.py` — dry-run safe; run weekly via cron)*
- [x] Add cleanup jobs for expired auth sessions if needed. *(`scripts/cleanup_expired_auth_sessions.py` — dry-run safe; run weekly via cron)*
- [x] Verify cleanup jobs do not break active users. *(`test_cleanup_stale_workspaces.py::test_main_keeps_active_workspace` confirms active-session paths are never deleted; `--dry-run` flag for safe pre-run verification)*

## 13. Testing

- [x] Run backend unit and integration tests before deploy. *(22 test files in `backend/tests/`; run `pytest backend/tests/`)*
- [ ] Run frontend tests before deploy. *(frontend uses Node `--test` runner; run `npm test` in `frontend/`)*
- [x] Add E2E tests for GitHub connect. *(`tests/e2e/` — Playwright framework + `helpers/api.js` `connectGithubViaPat`; specs: `specs/01_github_connect.spec.js`)*
- [x] Add E2E tests for provider credential add. *(`tests/e2e/helpers/api.js` `createProviderCredentialViaApi`; specs: `specs/02_provider_credential.spec.js`)*
- [x] Add E2E tests for session creation. *(`tests/e2e/helpers/api.js` `listSessionsViaApi`; specs: `specs/03_session_create.spec.js`)*
- [x] Add E2E tests for indexing to `ready`. *(`tests/e2e/helpers/api.js` `waitForSessionReady`; specs: `specs/04_indexing.spec.js`)*
- [x] Add E2E tests for query roundtrip. *(specs: `specs/05_query.spec.js`)*
- [x] Add E2E tests for chat reload persistence. *(specs: `specs/06_chat_persistence.spec.js`)*
- [x] Keep encrypted secret submission endpoint tests passing. *(`test_api_service_encrypted_submission.py`)*
- [x] Keep Postgres readiness validation passing. *(`test_postgres_readiness.py` + `scripts/validate_postgres_readiness.py`)*

## 14. Documentation and Runbooks

- [x] Keep deployment runbook current. *(`docs/deployment_runbook.md` — updated with env vars table, GitHub auth mode, rollback flow, cleanup jobs, known failure modes)*
- [x] Document production startup steps. *(runbook section 3 "Start Order")*
- [x] Document production restart steps. *(runbook section 5 "Restart Flow")*
- [x] Document production rollback steps. *(runbook section 6 "Rollback Flow")*
- [x] Document backup and restore steps. *(runbook section 7 "Backup Flow")*
- [x] Document GitHub auth mode choice. *(runbook section 2 "GitHub Auth Mode")*
- [x] Document required environment variables. *(runbook section 1 — full table of all env vars)*
- [x] Document known failure modes and operator responses. *(runbook section 10 "Known Failure Modes")*

## 15. Final Go-Live Checklist

- [ ] Deploy frontend and backend to the target environment. *(deployment stack now scaffolded with `docker-compose.deploy.yml`, `frontend/Dockerfile`, and `deploy/Caddyfile`)*
- [ ] Verify frontend can reach backend over HTTPS. *(use `scripts/smoke_test_deployment.sh` plus browser validation after deploy)*
- [ ] Create a real session from the deployed frontend.
- [ ] Allow the selected repo to index successfully.
- [ ] Run at least one successful query from the deployed frontend.
- [ ] Refresh the frontend and confirm session/chat persistence.
- [ ] Restart backend and confirm session/chat/provider persistence.
- [ ] Confirm monitoring is live.
- [ ] Confirm backups are enabled.
- [ ] Confirm no critical blockers remain.
