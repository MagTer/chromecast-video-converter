#!/usr/bin/env bash

set -euo pipefail

WATCH_ROOTS="${WATCH_ROOTS:-}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:9000}"
SCAN_INTERVAL="${SCAN_INTERVAL:-60}"

log() {
  local level="$1" message="$2"
  echo "$(date -Iseconds) [${level}] ${message}"
  if [[ -n "${ORCHESTRATOR_URL}" ]]; then
    local payload_message
    payload_message="${message//\"/\\\"}"
    curl -sS -X POST "${ORCHESTRATOR_URL}/api/logs/ingest" \
      -H "Content-Type: application/json" \
      -d "{\"entries\":[{\"logger\":\"folder-watcher\",\"level\":\"${level}\",\"message\":\"${payload_message}\"}]}" \
      >/dev/null 2>&1 || true
  fi
}

log_info() {
  log "INFO" "$1"
}

log_warn() {
  log "WARNING" "$1"
}

if [[ -z "${WATCH_ROOTS}" ]]; then
  log_warn "WATCH_ROOTS is required."
  exit 1
fi

declare -a ENTRIES
IFS="," read -ra RAW_ENTRIES <<< "${WATCH_ROOTS}"
for entry in "${RAW_ENTRIES[@]}"; do
  [[ -n "${entry}" ]] || continue
  ENTRIES+=("${entry}")
done

log_info "Folder watcher starting. Monitoring roots: ${WATCH_ROOTS}"
log_info "Reporting to orchestrator at ${ORCHESTRATOR_URL}"

while true; do
  for entry in "${ENTRIES[@]}"; do
    label="${entry%%:*}"
    path="${entry#*:}"
    if [[ -z "${label}" ]] || [[ -z "${path}" ]]; then
      continue
    fi
    if [[ ! -d "${path}" ]]; then
      log_warn "Root ${path} not available yet."
      continue
    fi

    log_info "Requesting scan for ${label} (${path})"
    curl -sSf -X POST "${ORCHESTRATOR_URL}/api/scan" \
      -H "Content-Type: application/json" \
      -d "{\"library\":\"${label}\",\"root\":\"${path}\"}" || true
  done
  sleep "${SCAN_INTERVAL}"
done
