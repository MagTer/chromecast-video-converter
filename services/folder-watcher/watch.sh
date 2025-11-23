#!/usr/bin/env bash

set -euo pipefail

WATCH_ROOTS="${WATCH_ROOTS:-}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:9000}"
EVENT_BUFFER_SECONDS="${EVENT_BUFFER_SECONDS:-0}"
EVENT_RETRY_ATTEMPTS="${EVENT_RETRY_ATTEMPTS:-5}"
EVENT_RETRY_BACKOFF_SECONDS="${EVENT_RETRY_BACKOFF_SECONDS:-2}"
ROOT_RETRY_SECONDS="${ROOT_RETRY_SECONDS:-5}"

log() {
  local level="$1" message="$2"
  echo "$(date -Iseconds) [${level}] ${message}"
  if [[ -n "${ORCHESTRATOR_URL}" ]]; then
    local payload_message
    payload_message="${message//\"/\\\"}"
    curl -sS -X POST "${ORCHESTRATOR_URL}/api/logs/ingest" \
      -H "Content-Type: application/json" \
      -d "{\"entries\":[{\"logger\":\"folder-watcher\",\"level\":\"${level}\",\"severity\":\"${level}\",\"source\":\"folder-watcher\",\"category\":\"events\",\"message\":\"${payload_message}\"}]}" \
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

declare -a EVENT_QUEUE=()

flush_queue() {
  if [[ ${#EVENT_QUEUE[@]} -eq 0 ]]; then
    return
  fi

  local joined payload
  joined=$(printf ",%s" "${EVENT_QUEUE[@]}")
  joined="${joined:1}"
  payload="{\"events\":[${joined}]}"
  EVENT_QUEUE=()
  send_payload "${payload}"
}

trap flush_queue EXIT

send_payload() {
  local payload="$1" attempt=1 delay="${EVENT_RETRY_BACKOFF_SECONDS}"
  while true; do
    if curl -sSf -X POST "${ORCHESTRATOR_URL}/api/events" \
      -H "Content-Type: application/json" \
      -d "${payload}" >/dev/null; then
      return 0
    fi

    if (( attempt >= EVENT_RETRY_ATTEMPTS )); then
      log_warn "Failed to post events after ${attempt} attempts"
      return 1
    fi

    log_warn "Event post failed (attempt ${attempt}); retrying in ${delay}s"
    attempt=$(( attempt + 1 ))
    sleep "${delay}"
    delay=$(( delay * 2 ))
  done
}

enqueue_event() {
  local event_json="$1"
  if (( EVENT_BUFFER_SECONDS > 0 )); then
    EVENT_QUEUE+=("${event_json}")
  else
    send_payload "{\"events\":[${event_json}]}"
  fi
}

serialize_event() {
  local library="$1" path="$2" event_type="$3" is_dir="$4"
  local size="null" modified_at="null"

  if [[ "${is_dir}" == "false" && -f "${path}" ]]; then
    size=$(stat -c %s "${path}")
    modified_at="\"$(date -Iseconds -r "${path}")\""
  fi

  local escaped_path escaped_library
  escaped_path=${path//\"/\\\"}
  escaped_library=${library//\"/\\\"}
  echo "{\"path\":\"${escaped_path}\",\"library\":\"${escaped_library}\",\"event\":\"${event_type}\",\"size\":${size},\"modified_at\":${modified_at},\"is_directory\":${is_dir}}"
}

watch_root() {
  local label="$1" path="$2"

  while [[ ! -d "${path}" ]]; do
    log_warn "Root ${path} not available yet. Waiting ${ROOT_RETRY_SECONDS}s"
    sleep "${ROOT_RETRY_SECONDS}"
  done

  log_info "Starting watcher for ${label} at ${path}"

  inotifywait -m -r -e create -e modify -e close_write -e moved_to -e delete -e moved_from -e attrib --format '%e|%w|%f' "${path}" |
    while IFS='|' read -r events directory file; do
      event_type="modified"
      if [[ "${events}" == *"DELETE"* ]] || [[ "${events}" == *"MOVED_FROM"* ]]; then
        event_type="deleted"
      elif [[ "${events}" == *"CREATE"* ]] || [[ "${events}" == *"MOVED_TO"* ]]; then
        event_type="created"
      fi

      is_dir="false"
      if [[ "${events}" == *"ISDIR"* ]]; then
        is_dir="true"
      fi

      full_path="${directory}${file}"
      enqueue_event "$(serialize_event "${label}" "${full_path}" "${event_type}" "${is_dir}")"
    done &
}

for entry in "${ENTRIES[@]}"; do
  label="${entry%%:*}"
  path="${entry#*:}"
  if [[ -z "${label}" ]] || [[ -z "${path}" ]]; then
    continue
  fi
  watch_root "${label}" "${path}"
done

if (( EVENT_BUFFER_SECONDS > 0 )); then
  while true; do
    sleep "${EVENT_BUFFER_SECONDS}"
    flush_queue
  done
else
  wait
fi
