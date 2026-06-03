# Codeseek Deployment Runbook

This runbook covers the minimum production shape for a personal or small-group Codeseek deployment.

## 1. Environment

Copy [.env.example](/home/arch/DEV/CodeSeek/backend/.env.example) to `.env` and set:

- `CODESEEK_API_KEY` to a random backend bearer token
- `CODESEEK_APP_ENCRYPTION_KEY` to a dedicated application encryption key
- `CODESEEK_DB_BACKEND=postgres`
- `CODESEEK_DATABASE_URL` to the Postgres DSN
- `CODESEEK_AUTH_SESSION_SECURE_COOKIE=1`
- `CODESEEK_ENFORCE_HTTPS=1`
- `CODESEEK_ALLOW_PLAINTEXT_SECRET_SUBMISSION=0`
- `CODESEEK_CORS_ORIGINS` to the deployed frontend origin
- GitHub OAuth variables if OAuth login is enabled

If TLS is terminated by a reverse proxy, forward `X-Forwarded-Proto: https` to the backend.

## 2. Start Order

1. Start Postgres and wait for health.
2. Start Qdrant and wait for health.
3. Start `codeseek-api`.
4. Start the frontend behind HTTPS.

Using Docker Compose:

```bash
docker compose up -d postgres qdrant codeseek-api
```

Verify:

```bash
docker compose ps
curl http://127.0.0.1:8000/api/v1/health
```

Expected result: backend status is `ok` or `degraded`, never connection-refused.

## 3. HTTPS / Reverse Proxy

Codeseek should not be exposed directly on plain HTTP in deployment.

- Terminate TLS at Nginx, Caddy, Traefik, or a cloud load balancer.
- Proxy `/api/`, `/auth/`, `/health`, and `/metrics` to `codeseek-api:8000`.
- Keep the backend on a private network.
- Set secure cookies and keep `CODESEEK_ENFORCE_HTTPS=1`.

## 4. Restart Flow

Safe restart order:

1. Restart frontend or reverse proxy.
2. Restart `codeseek-api`.
3. Restart Postgres or Qdrant only when required.

Commands:

```bash
docker compose restart codeseek-api
docker compose logs --tail=200 codeseek-api
```

After restart, verify:

- `GET /api/v1/health` responds
- existing GitHub auth session still works
- provider credentials still list correctly
- existing repo sessions and chat threads still load

## 5. Backup Flow

### Postgres

Create a logical backup:

```bash
docker compose exec postgres pg_dump -U codeseek -d codeseek > codeseek-postgres.sql
```

Restore:

```bash
cat codeseek-postgres.sql | docker compose exec -T postgres psql -U codeseek -d codeseek
```

### Qdrant

Use the bundled scripts:

```bash
./.venv/bin/python scripts/qdrant_snapshot_backup.py --output-dir /tmp/qdrant-backups
./.venv/bin/python scripts/qdrant_snapshot_restore.py --snapshot /tmp/qdrant-backups/<snapshot-name>
```

Schedule recurring Qdrant backups with:

```bash
./.venv/bin/python scripts/qdrant_snapshot_schedule.py --output-dir /tmp/qdrant-backups
```

## 6. Monitoring

Minimum checks:

- backend: `GET /api/v1/health`
- backend metrics: `GET /api/v1/metrics`
- Postgres: `pg_isready`
- Qdrant: `GET /healthz`

Alert on:

- repeated `401` or auth-expired complaints
- repeated `429` provider failures
- sessions stuck in `indexing`
- sessions moving to `failed`
- Qdrant or Postgres healthcheck failures

## 7. Smoke Checklist

Run this after each deployment:

1. Sign in with GitHub.
2. Add a provider credential.
3. Create a repo session.
4. Wait for indexing to reach `ready`.
5. Ask a query and confirm sources render.
6. Refresh the browser and confirm chat/session persistence.

## 8. Postgres Readiness Validation

For a focused persistence validation outside the full UI flow, run:

```bash
PYTHONPATH=/home/arch/DEV/CodeSeek/backend \
CODESEEK_DB_BACKEND=postgres \
CODESEEK_DATABASE_URL=postgresql://codeseek:codeseek@localhost:5432/codeseek \
CODESEEK_APP_ENCRYPTION_KEY=replace-with-real-key \
./.venv/bin/python scripts/validate_postgres_readiness.py
```

This verifies:

- schema creation in Postgres
- `users`, provider credential, repo session, thread, message, and memory row creation
- persistence across backend re-init
- no accidental SQLite file usage in Postgres mode
