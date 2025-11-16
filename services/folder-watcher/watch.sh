#!/usr/bin/env bash

set -euo pipefail

WATCH_ROOTS="${WATCH_ROOTS:-}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:9000}"
SCAN_INTERVAL="${SCAN_INTERVAL:-60}"

if [[ -z "${WATCH_ROOTS}" ]]; then
  echo "WATCH_ROOTS is required."
  exit 1
fi

declare -a ENTRIES
IFS="," read -ra RAW_ENTRIES <<< "${WATCH_ROOTS}"
for entry in "${RAW_ENTRIES[@]}"; do
  [[ -n "${entry}" ]] || continue
  ENTRIES+=("${entry}")
done

echo "Folder watcher starting. Monitoring roots: ${WATCH_ROOTS}"
echo "Reporting to orchestrator at ${ORCHESTRATOR_URL}"

while true; do
  for entry in "${ENTRIES[@]}"; do
    label="${entry%%:*}"
    path="${entry#*:}"
    if [[ -z "${label}" ]] || [[ -z "${path}" ]]; then
      continue
    fi
    if [[ ! -d "${path}" ]]; then
      echo "Root ${path} not available yet."
      continue
    fi

    echo "Requesting scan for ${label} (${path})"
    curl -sSf -X POST "${ORCHESTRATOR_URL}/api/scan" \
      -H "Content-Type: application/json" \
      -d "{\"library\":\"${label}\",\"root\":\"${path}\"}" || true
  done
  sleep "${SCAN_INTERVAL}"
done
