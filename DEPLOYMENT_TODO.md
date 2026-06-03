# Deployment Readiness Checklist

Use this document as the single deployment checklist for Codeseek.

## 1. Infrastructure

- [ ] Provision the deployment environment for frontend, backend, Postgres, and Qdrant.
- [ ] Verify Docker / container runtime is available in the target environment.
- [ ] Configure persistent storage for Postgres.
- [ ] Configure persistent storage for Qdrant.
- [ ] Ensure backend repo workspace storage is available and writable.
- [ ] Confirm required ports and internal network access are available.
- [ ] Configure process supervision for backend services.
- [ ] Verify service restart behavior after host/container restart.

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
- [ ] Confirm no local-development placeholder values remain in `.env`.
- [ ] Confirm secrets are injected through the deployment platform, not committed files.

## 3. Security

- [ ] Put the app behind HTTPS/TLS.
- [ ] Confirm TLS termination forwards `X-Forwarded-Proto: https` when needed.
- [ ] Verify secure auth cookies are set in deployment.
- [ ] Verify plaintext secret submission is disabled in deployment.
- [ ] Verify secret-bearing request bodies are not logged.
- [ ] Review backend logs for accidental token, key, cookie, or ciphertext exposure.
- [ ] Confirm GitHub access tokens are only stored server-side in encrypted form.
- [ ] Confirm provider API keys are only stored server-side in encrypted form.
- [ ] Review API endpoints for missing auth checks.
- [ ] Review API endpoints for missing ownership checks.
- [ ] Verify per-user session isolation.
- [ ] Verify per-user provider credential isolation.
- [ ] Verify per-user GitHub credential isolation.
- [ ] Verify per-user chat/thread isolation.

## 4. Database and Persistence

- [ ] Start the backend successfully with Postgres.
- [ ] Verify tables are created in Postgres.
- [ ] Verify no SQLite file is used in Postgres mode.
- [ ] Verify GitHub login creates a `users` row.
- [ ] Verify provider credential add creates a `user_provider_credentials` row.
- [ ] Verify session creation creates a `repo_sessions` row.
- [ ] Verify chat usage creates `chat_threads`, `chat_messages`, and `thread_memory` rows.
- [ ] Verify app restart preserves users, sessions, chats, and credentials.
- [ ] Verify duplicate session creation for the same user/repo returns the existing session.
- [ ] Verify session deletion behaves correctly.
- [ ] Verify retry indexing behaves correctly.

## 5. Qdrant and Indexing

- [ ] Verify Qdrant is reachable from the backend.
- [ ] Verify repo session creation triggers clone/pull and indexing.
- [ ] Verify indexing transitions from `indexing` to `ready`.
- [ ] Verify indexing failures transition to `failed` with usable error messages.
- [ ] Verify the selected repo at session creation is the repo that gets indexed.
- [ ] Verify duplicate sessions are not created for the same repo/user.
- [ ] Verify repo workspace reuse behaves correctly across repeated indexing.
- [ ] Verify indexed collections remain isolated per repo.
- [ ] Verify collection naming matches the repo/session isolation model.

## 6. Authentication and GitHub Integration

- [ ] Decide whether deployment will use GitHub OAuth, PAT connect, or both.
- [ ] Verify GitHub OAuth configuration end to end.
- [ ] Verify PAT connect configuration end to end.
- [ ] Verify GitHub session login works from the frontend.
- [ ] Verify GitHub repo listing works for the authenticated user.
- [ ] Verify GitHub auth failure states are clear in the UI.
- [ ] Verify expired auth-session handling and re-login flow.
- [ ] Verify logout clears the auth session correctly.

## 7. Provider Credential Flow

- [ ] Verify provider submission key endpoint works.
- [ ] Verify encrypted provider credential submission works from the frontend.
- [ ] Verify provider credentials list correctly after save.
- [ ] Verify provider credential activation works.
- [ ] Verify provider credential deletion works.
- [ ] Verify provider credentials survive browser refresh.
- [ ] Verify provider credentials survive backend restart.
- [ ] Verify missing provider credential state is clear in the UI.
- [ ] Verify invalid provider configuration state is clear in the UI.
- [ ] Verify provider rate-limit (`429`) state is clear in the UI.

## 8. Query and Chat Flow

- [ ] Verify query roundtrip works from the frontend.
- [ ] Verify chat history persists after refresh.
- [ ] Verify chat history persists after backend restart.
- [ ] Verify thread memory persists correctly.
- [ ] Verify hidden thread behavior does not leak or clear the wrong messages.
- [ ] Verify low-context fallback responses are acceptable.
- [ ] Verify overview queries produce acceptable answers.
- [ ] Verify tech-stack queries produce acceptable answers.
- [ ] Verify explanation-mode queries produce acceptable answers.
- [ ] Verify section queries that depend on imported data produce acceptable answers.

## 9. UI and UX

- [ ] Verify indexing progress is visible and understandable.
- [ ] Verify indexing failure UI provides actionable retry guidance.
- [ ] Verify expired auth state is visible and recoverable.
- [ ] Verify missing provider configuration guidance is visible and actionable.
- [ ] Verify session list behavior is correct after creation, deletion, and reuse.
- [ ] Verify duplicate repo selection returns the existing session cleanly in the UI.
- [ ] Verify mobile layout is usable for session and chat flows.
- [ ] Verify source rendering is readable.
- [ ] Verify source copy / follow-up behavior is acceptable for deployment.

## 10. Observability and Operations

- [ ] Verify `/api/v1/health` works in deployment.
- [ ] Verify `/api/v1/metrics` works in deployment.
- [ ] Add monitoring for backend health.
- [ ] Add monitoring for Postgres health.
- [ ] Add monitoring for Qdrant health.
- [ ] Add alerting for backend startup failure.
- [ ] Add alerting for indexing failures.
- [ ] Add alerting for repeated provider failures.
- [ ] Add alerting for repeated auth failures.
- [ ] Add alerting for rate-limit spikes.
- [ ] Review structured logs for deployment usefulness.

## 11. Backups and Recovery

- [ ] Verify Postgres backup procedure works.
- [ ] Verify Postgres restore procedure works.
- [ ] Verify Qdrant snapshot backup procedure works.
- [ ] Verify Qdrant snapshot restore procedure works.
- [ ] Define backup schedule.
- [ ] Define retention policy for backups.
- [ ] Test recovery from backup in a non-production environment.

## 12. Cleanup and Retention

- [ ] Define retention policy for stale repo sessions.
- [ ] Define retention policy for old repo workspaces.
- [ ] Define retention policy for expired auth sessions.
- [ ] Add cleanup jobs for stale repo workspaces.
- [ ] Add cleanup jobs for expired auth sessions if needed.
- [ ] Verify cleanup jobs do not break active users.

## 13. Testing

- [ ] Run backend unit and integration tests before deploy.
- [ ] Run frontend tests before deploy.
- [ ] Add E2E tests for GitHub connect.
- [ ] Add E2E tests for provider credential add.
- [ ] Add E2E tests for session creation.
- [ ] Add E2E tests for indexing to `ready`.
- [ ] Add E2E tests for query roundtrip.
- [ ] Add E2E tests for chat reload persistence.
- [ ] Keep encrypted secret submission endpoint tests passing.
- [ ] Keep Postgres readiness validation passing.

## 14. Documentation and Runbooks

- [ ] Keep deployment runbook current.
- [ ] Document production startup steps.
- [ ] Document production restart steps.
- [ ] Document production rollback steps.
- [ ] Document backup and restore steps.
- [ ] Document GitHub auth mode choice.
- [ ] Document required environment variables.
- [ ] Document known failure modes and operator responses.

## 15. Final Go-Live Checklist

- [ ] Deploy frontend and backend to the target environment.
- [ ] Verify frontend can reach backend over HTTPS.
- [ ] Create a real session from the deployed frontend.
- [ ] Allow the selected repo to index successfully.
- [ ] Run at least one successful query from the deployed frontend.
- [ ] Refresh the frontend and confirm session/chat persistence.
- [ ] Restart backend and confirm session/chat/provider persistence.
- [ ] Confirm monitoring is live.
- [ ] Confirm backups are enabled.
- [ ] Confirm no critical blockers remain.
