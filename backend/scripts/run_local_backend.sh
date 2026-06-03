#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STARTUP_REPO_ROOT="${1:-$ROOT_DIR}"

cd "$ROOT_DIR"

docker compose up -d qdrant

set -a
source "$ROOT_DIR/.env"
set +a

export CODESEEK_TENANT_ID="${CODESEEK_TENANT_ID:-local}"
export RETRIEVAL_REPO_ROOT="$STARTUP_REPO_ROOT"

exec ./.venv/bin/uvicorn retrieval.api_service:app --host 0.0.0.0 --port 8000
