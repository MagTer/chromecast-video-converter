#!/usr/bin/env bash

set -euo pipefail

PORT="${PORT:-9000}"

# Align alembic target with the orchestrator's library/config database where
# encoding profiles live (LIBRARY_DB_PATH). This must match app.main Engine.
if [[ -n "${LIBRARY_DB_PATH:-}" ]]; then
  DB_PATH="${LIBRARY_DB_PATH}"
else
  DB_PATH="/app/data/library.db"
fi

DATABASE_URL="${DATABASE_URL:-sqlite:////${DB_PATH#'/'}}"
export DATABASE_URL LIBRARY_DB_PATH

if [[ "${DATABASE_URL}" == sqlite:* ]]; then
  python3 - <<'PY'
import os
from pathlib import Path

url = os.environ["DATABASE_URL"]
if url.startswith("sqlite"):
    path = url.split("sqlite://", maxsplit=1)[-1]
    if path.startswith("/"):
        normalized = "/" + path.lstrip("/")
    else:
        normalized = path.lstrip(":/")
        normalized = "/" + normalized
    Path(normalized).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
PY
fi

alembic -c /app/alembic.ini upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
