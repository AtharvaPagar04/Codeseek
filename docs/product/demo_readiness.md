# CodeSeek Demo Readiness (V1)

This guide provides steps and details to verify that a local instance of CodeSeek is ready for demonstrating to teams or users.

---

## 1. System Requirements Check

Ensure the following prerequisites are installed and active on the local machine:
- **Python:** Version 3.11
- **Node.js:** Version 18+ (with npm)
- **Docker / Docker Compose:** For running Qdrant and (optional) Postgres database services.

---

## 2. Infrastructure Setup & Verification

### A. Vector database (Qdrant)
Start Qdrant via Docker Compose:
```bash
docker compose up -d qdrant
```
Verify accessibility of Qdrant by querying its health endpoint:
```bash
curl http://localhost:6333/healthz
```
Expected response: HTTP status `200` with JSON body containing health info.

### B. Relational Database (SQLite / Postgres)
CodeSeek uses SQLite by default for simple local setup. If running SQLite, verify that `backend/codeseek.db` is present after startup.
If running with Postgres:
```bash
docker compose up -d postgres
```
Verify the migration status by executing:
```bash
python backend/scripts/migrate.py
```

---

## 3. Launching Services

### A. FastAPI Backend
Run the backend with reload features enabled for developer debugging:
```bash
cd backend
./scripts/run_local_backend.sh
```
The backend API is hosted at: `http://localhost:8000`

Verify API health:
```bash
curl http://localhost:8000/api/v1/health
```

### B. React Frontend SPA
Start the dev server:
```bash
cd frontend
npm install
npm run dev
```
The frontend is hosted at: `http://localhost:5173`

---

## 4. Key Environmental Flags

During demonstrations, you can toggle these environment variables in your `backend/.env` file:

| Flag | Default | Description |
|---|---|---|
| `CODESEEK_ENABLE_INCREMENTAL_REINDEX` | `false` | Set to `true` to enable the experimental **Index changed files** button in the UI. |
| `CODESEEK_DB_BACKEND` | `sqlite` | Select backend database driver (`sqlite` or `postgres`). |
| `CODESEEK_RATE_LIMIT_PER_MINUTE` | `60` | Configure request rate limit. |
| `CODESEEK_ALLOW_PLAINTEXT_SECRET_SUBMISSION` | `1` | Allows plaintext secret submission for easy testing. |

---

## 5. Development Validation Policy

When validating changes in local workspace, keep the following rules in mind:
- **No full pytest by default:** Do not run full regression test suites. Instead, target specific modules (e.g., `pytest backend/tests/test_freshness.py`).
- **No safe eval:** Do not execute automated evaluation pipelines (RAGAS calibration) during validation unless explicitly requested by the task requirements.
