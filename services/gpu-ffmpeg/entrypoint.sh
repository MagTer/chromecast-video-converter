#!/usr/bin/env bash

set -euo pipefail

echo "gpu-ffmpeg worker starting, pointing at ${ORCHESTRATOR_URL:-http://localhost:9000}"
python3 worker.py
