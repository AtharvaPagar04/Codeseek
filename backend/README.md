# Codeseek

Repository-grounded RAG assistant for source code.

Codeseek has two core pipelines:
- Ingestion: scans a repository, parses code, creates chunks, embeds them, stores in Qdrant.
- Retrieval: takes a query, searches/expands relevant chunks, assembles context, generates grounded answers with citations.

## Project Status

Current local status is production-baseline:
- Multi-repo support with strict tenant/repo collection isolation.
- FastAPI service with versioned endpoints (`/api/v1/*`).
- Security baseline (auth, rate limit, secret scan).
- Reliability controls (timeouts, retries, circuit breakers, degraded fallback).
- Observability (structured logs, request IDs, Prometheus metrics endpoint).
- CI quality gates (retrieval thresholds, API black-box checks, load smoke).
- Deployment support (Docker, compose, release workflow, snapshot backup/restore + schedule).

## Quick Start (Local)

1. Install dependencies:

```bash
cd /home/arch/DEV/RAG/Codeseek
# Use Python 3.11 for compatibility (for example, tiktoken wheels).
uv python install 3.11
uv venv --clear --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

2. Configure environment:

```bash
cp .env.example .env
```

SQLite is the default local persistence backend. To run with Postgres instead, set:

```bash
CODESEEK_DB_BACKEND=postgres
CODESEEK_DATABASE_URL=postgresql://codeseek:codeseek@localhost:5432/codeseek
```

3. Start infrastructure:

```bash
docker compose up -d qdrant
```

For Postgres-backed local runs:

```bash
docker compose up -d postgres qdrant
```

4. Ingest a repo:

```bash
CODESEEK_TENANT_ID=local \
INGESTION_ENABLE_INCREMENTAL_FILE_SKIP=0 \
QDRANT_RECREATE_COLLECTION=1 \
./.venv/bin/python -m rag_ingestion.main /tmp/trading-bot-e2e
```

5. Query via CLI:

```bash
CODESEEK_TENANT_ID=local \
RETRIEVAL_REPO_ROOT=/tmp/trading-bot-e2e \
./.venv/bin/python -m retrieval.main \
  --query "Trace account_info() to final HTTP request and where signature/API key are attached."
```

6. Run API:

```bash
set -a && source .env && set +a
CODESEEK_TENANT_ID=local \
RETRIEVAL_REPO_ROOT=/tmp/trading-bot-e2e \
./.venv/bin/uvicorn retrieval.api_service:app --host 0.0.0.0 --port 8000
```

7. Query API:

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Authorization: Bearer $CODESEEK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"Which method computes Binance HMAC SHA256 signature?"}'
```

## API Endpoints

- `GET /api/v1/health`
- `POST /api/v1/query`
- `POST /api/v1/sessions` (create session + start async clone/pull+ingestion job)
- `GET /api/v1/sessions` (list sessions + status)
- `GET /api/v1/sessions/{session_id}` (single session status/details)
- `GET /api/v1/metrics` (Prometheus)

Backward-compatible aliases:
- `/health`
- `/query`
- `/metrics`

Session initialization flow:
- Create session: `POST /api/v1/sessions` with `repo_full_name` (`owner/repo`) and optional `repo_url`.
- Backend immediately returns `status=indexing`.
- Background worker clones/pulls repo, performs repo-scoped ingestion, and updates status to `ready` or `failed`.
- Query can include `session_id`; backend rejects requests while session is not `ready`.

## Docs

- Project docs index: [docs/README.md](docs/README.md)
- Ingestion docs: `docs/ingestion_docs/*`
- Retrieval docs: `docs/retrieval_docs/*`

## Operations

- Secret scan: `python scripts/scan_secrets.py`
- Load smoke: `python scripts/load_test_api.py ...`
- Snapshot backup: `python scripts/qdrant_snapshot_backup.py ...`
- Snapshot restore: `python scripts/qdrant_snapshot_restore.py ...`
- Scheduled backup + retention: `python scripts/qdrant_snapshot_schedule.py ...`

## CI / Release

- Retrieval regression + API integration gates:
  - `.github/workflows/retrieval-regression.yml`
- Scheduled snapshot workflow:
  - `.github/workflows/qdrant-snapshot-schedule.yml`
- Versioned image release workflow (GHCR):
  - `.github/workflows/release-image.yml`
