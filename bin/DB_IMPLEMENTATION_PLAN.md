# DB Implementation Plan

This document defines the target persistence architecture for Codeseek as it moves from local/frontend state to a production-grade backend data model suitable for personal or small multi-user deployment.

## Implementation Status

Current status for the migration:

- [x] Backend DB foundation added using a first-pass SQLite implementation
- [x] Backend repo session persistence moved off `/tmp/codeseek_sessions.json`
- [x] Backend chat message persistence added for session queries
- [x] Frontend now hydrates session chat history from backend message APIs
- [x] Frontend local chat storage removed as a dependency
- [x] User/auth tables implemented
- [x] GitHub credentials moved server-side
- [x] Provider credentials moved server-side
- [x] Rolling thread memory implemented
- [x] First-class `chat_threads` implemented end-to-end
- [x] Backend persistence layer can now run on SQLite or Postgres

Note:

- The long-term target remains Postgres-backed persistence for deployed multi-user usage.
- The app now supports both SQLite and Postgres through the same store layer, with SQLite remaining the default local path.
- This should be treated as a Phase 1 persistence landing, not the final production database architecture.
- Frontend session/chat state is now runtime-only UI state; the durable source of truth is the backend message/session APIs.
- A backend auth foundation now exists: `users`, `auth_sessions`, GitHub OAuth session-cookie issuance, `/auth/me`, and `/auth/logout`.
- GitHub credentials are now stored server-side and used for backend repo listing + session indexing.
- Provider credentials are now stored server-side and selected through a backend-owned active configuration per user.
- Session-level rolling memory is now persisted in the database using a summary + recent-turn history model.
- Chat data is now modeled through first-class `chat_threads`, with message and memory ownership attached to threads instead of directly to repo sessions.

## Goal

Replace local-only storage and process-local memory with a backend-managed database design that supports:

- persistent chat history
- user login/logout
- encrypted storage of user credentials
- per-user repo sessions
- conversation context across previous chats
- future deployment stability

## Current Problems

The current architecture still has one main deployment gap:

- SQLite remains the default local database, while Postgres should be the deployed default for stronger concurrency and operations

The remaining work is now mostly operational migration and deployment choice rather than application data-model redesign.

## Recommended Stack

Use:

- `Postgres` as the primary database
- optional `Redis` later for short-lived cache, rate limiting, or background coordination
- backend-managed encrypted secret storage for provider keys and GitHub tokens

Postgres is enough for the first full migration.

## What Must Move To The Database

The following data should stop living in frontend storage or `/tmp` files:

- users
- auth sessions
- GitHub OAuth tokens
- LLM provider credentials
- repo indexing sessions
- chat threads
- chat messages
- long-term conversation summaries

## Core Data Model

### 1. `users`

Purpose:

- application identity for each logged-in user

Recommended fields:

- `id`
- `github_user_id`
- `username`
- `avatar_url`
- `created_at`
- `updated_at`

Notes:

- `github_user_id` should be unique
- this becomes the owner link for credentials, repo sessions, and chats

### 2. `auth_sessions`

Purpose:

- server-side login session management

Recommended fields:

- `id`
- `user_id`
- `session_token_hash`
- `expires_at`
- `created_at`
- `last_seen_at`

Notes:

- do not store raw session tokens
- store hash only
- issue session cookie from backend as `HttpOnly`

### 3. `user_github_credentials`

Purpose:

- encrypted per-user GitHub access token storage

Recommended fields:

- `id`
- `user_id`
- `github_login`
- `encrypted_access_token`
- `token_type`
- `scope_info`
- `created_at`
- `updated_at`

Notes:

- token must be encrypted, not hashed
- backend needs the raw value after decrypting to call GitHub APIs and clone private repos

### 4. `user_provider_credentials`

Purpose:

- encrypted per-user LLM provider config storage

Recommended fields:

- `id`
- `user_id`
- `provider`
- `label`
- `encrypted_api_key`
- `model`
- `is_active`
- `created_at`
- `updated_at`

Notes:

- supports providers like `groq`, `gemini`, `openai`, `openrouter`
- no provider key should remain in frontend localStorage in the final design

### 5. `repo_sessions`

Purpose:

- persistent record of indexed repositories per user

Recommended fields:

- `id`
- `user_id`
- `repo_full_name`
- `repo_url`
- `repo_root`
- `collection`
- `status`
- `error`
- `last_indexed_commit`
- `chunks_generated`
- `embeddings_stored`
- `idempotent_reuse`
- `created_at`
- `updated_at`

Notes:

- replaces `/tmp/codeseek_sessions.json`
- should preserve current indexing lifecycle semantics

### 6. `chat_threads`

Purpose:

- logical conversation container for repo-specific chat

Recommended fields:

- `id`
- `user_id`
- `repo_session_id`
- `title`
- `created_at`
- `updated_at`

Notes:

- one repo session may have multiple threads in the future
- can start with one thread per repo session if needed

### 7. `chat_messages`

Purpose:

- persistent per-thread messages

Recommended fields:

- `id`
- `thread_id`
- `role`
- `content`
- `sources_json`
- `context_tokens`
- `created_at`

Notes:

- `role` should support at least `user` and `assistant`
- `sources_json` can keep current source payload shape
- preserves UI history across refresh and deployment restarts

### 8. `thread_memory`

Purpose:

- long-term conversation context summary

Recommended fields:

- `thread_id`
- `rolling_summary`
- `last_compacted_at`

Notes:

- this is not a replacement for recent messages
- it is the compacted memory layer for older turns

## Credential Storage Rules

### Hash vs Encrypt

Use:

- hash for session tokens or passwords
- encryption for provider keys and GitHub access tokens

Reason:

- hashed values cannot be used again
- provider/GitHub tokens must be decrypted when making outbound API calls

### Encryption Recommendation

Use one application-level encryption key, stored in server env or a secret manager:

- `APP_ENCRYPTION_KEY`

Good practical approach:

- Fernet for simple implementation
- AES-GCM if lower-level control is needed later

### Security Rules

Must do:

- never store raw credentials in frontend localStorage in final design
- never log decrypted secrets
- never return stored secrets to the frontend after initial creation
- never write raw provider or GitHub tokens into `/tmp` state files

## Recommended Auth Model

Use GitHub OAuth as login.

Target flow:

1. user clicks `Login with GitHub`
2. backend redirects to GitHub OAuth
3. GitHub redirects back to backend callback
4. backend exchanges code for access token
5. backend stores GitHub token encrypted in DB
6. backend creates server auth session
7. backend sets `HttpOnly` session cookie
8. frontend uses session cookie, not raw GitHub token
9. logout clears server session and invalidates cookie

This also allows:

- fetching repos from backend
- cloning private repos server-side using user-scoped GitHub credentials

## Conversation Context Strategy

Do not send the full historical chat every time.

Use a hybrid approach:

### Short-term context

Load recent messages directly from `chat_messages`.

Recommended:

- last `8-12` messages

### Long-term context

Load a compact rolling summary from `thread_memory`.

Recommended:

- summarize older turns after every `N` exchanges
- keep latest turns verbatim
- prepend rolling summary before recent turns when building retrieval context

### Prompt assembly target

For each new query:

- fetch `thread_memory.rolling_summary`
- fetch latest recent messages
- fetch repo retrieval context
- build prompt from summary + recent turns + retrieved code context

This gives:

- persistent session context
- lower token usage
- better follow-up handling
- stable memory across restarts

## Backend API Changes Needed

### Auth

- `GET /auth/github/start`
- `GET /auth/github/callback`
- `GET /auth/me`
- `POST /auth/logout`

### GitHub

- `GET /api/v1/github/repos`

### Provider Credentials

- `GET /api/v1/provider-configs`
- `POST /api/v1/provider-configs`
- `DELETE /api/v1/provider-configs/{id}`
- `POST /api/v1/provider-configs/{id}/activate`

### Chat Threads

- `GET /api/v1/threads`
- `POST /api/v1/threads`
- `GET /api/v1/threads/{id}/messages`
- `POST /api/v1/threads/{id}/messages`

### Repo Sessions

- existing repo session APIs should be migrated to DB-backed persistence

## Frontend Changes Needed

### Remove frontend-owned persistence for:

- chat messages
- GitHub token
- provider keys

### Replace with backend-driven state:

- fetch current user with `/auth/me`
- fetch repo list from backend
- fetch chat threads/messages from backend
- create/send messages through backend
- manage provider configs through backend APIs

### Keep only lightweight frontend state for:

- UI state
- open modal state
- current input text
- optimistic pending message state if desired

## Persistence Migration Targets

### Replace

- `/tmp/codeseek_sessions.json`
- frontend `localStorage` chat persistence
- process-local `ConversationMemory`

### With

- Postgres-backed repo sessions
- Postgres-backed threads/messages
- Postgres-backed rolling summaries
- encrypted DB credential records

## Recommended Python Stack

Use:

- `SQLAlchemy`
- `Alembic`

Reason:

- stable
- explicit
- production-friendly
- easy to control migrations

## Suggested Implementation Phases

### Phase 1: DB Foundation

Implement:

- Postgres connection
- SQLAlchemy models
- Alembic migrations
- `users`
- `repo_sessions`
- `chat_threads`
- `chat_messages`

Outcome:

- chats and repo sessions are no longer local-only

### Phase 2: Auth + GitHub Credential Storage

Implement:

- GitHub OAuth login/logout
- `auth_sessions`
- `user_github_credentials`
- backend `/auth/me`
- backend `/api/v1/github/repos`

Outcome:

- no GitHub token in frontend localStorage

### Phase 3: Provider Credential Storage

Implement:

- `user_provider_credentials`
- encrypted provider key storage
- provider config CRUD APIs
- active provider selection per user

Outcome:

- no provider key in frontend localStorage

### Phase 4: Conversation Memory

Implement:

- `thread_memory`
- rolling summary updater
- prompt builder that uses summary + recent messages

Outcome:

- previous chat context works across sessions and deployments

## Immediate Priorities

The best next implementation order is:

1. move repo sessions and chats to Postgres
2. add backend auth session model
3. move GitHub credentials server-side
4. move provider credentials server-side
5. replace process-local conversation memory with DB-backed thread memory

## Final Target State

When this migration is complete:

- users log in via GitHub OAuth
- backend owns auth session and GitHub token
- provider keys are encrypted in DB
- chats persist in DB
- repo sessions persist in DB
- previous chat context is preserved via recent messages + rolling summary
- frontend becomes a UI client, not the source of truth for persistent state
