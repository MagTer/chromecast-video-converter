#!/usr/bin/env bash

set -euo pipefail

PORT="${PORT:-8080}"
export FLASK_APP=app.app:app

exec flask run --host 0.0.0.0 --port "${PORT}"
