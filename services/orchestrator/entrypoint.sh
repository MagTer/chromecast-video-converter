#!/usr/bin/env bash

set -euo pipefail

PORT="${PORT:-9000}"

DATA_DIR="${DATA_DIR:-/app/data}"
mkdir -p "${DATA_DIR}"

if [[ -n "${LIBRARY_DB_PATH:-}" ]]; then
  DB_PATH="${LIBRARY_DB_PATH}"
else
  DB_PATH="${DATA_DIR%/}/library.db"
fi

DATABASE_URL="${DATABASE_URL:-sqlite:////${DB_PATH#'/'}}"
export DATABASE_URL LIBRARY_DB_PATH DATA_DIR

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
