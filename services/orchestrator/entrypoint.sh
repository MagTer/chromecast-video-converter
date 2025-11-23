#!/usr/bin/env bash

set -euo pipefail

PORT="${PORT:-9000}"
DATABASE_URL="${DATABASE_URL:-sqlite:////app/config/orchestrator.db}"
export DATABASE_URL

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
