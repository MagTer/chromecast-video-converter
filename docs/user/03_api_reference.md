# 03 - API Reference (MVP)

This cheatsheet covers the orchestrator endpoints most operators and scripts will touch. Paths are relative to the orchestrator base URL (default `http://localhost:9000`).

## Libraries

- **List**: `GET /api/libraries`
- **Create**: `POST /api/libraries`

  ```json
  {
    "name": "movies",
    "root": "/media/movies",
    "profile_id": 1
  }
  ```

  Constraints: `name` unique; `root` non-empty (use mounted `/media/...` or `/watch/...`, the API normalizes to `/media/...` when responding); `profile_id` must exist. Depth always defaults to `"max"` so the entire tree is scanned.

- **Delete**: `DELETE /api/libraries/{name}` — Removes library config and marks existing entries from that library as `removed`.

- **Update profile**: `PATCH /api/libraries/{name}` with `{ "profile_id": <id> }`.

## Library entries

- **List (paginated)**: `GET /api/library/entries?limit=100&offset=0&include_total=true&status=<optional>&library=<optional>`

  Returns either an array or an object `{items, total, limit, offset}` when `include_total=true`. Default order: `updated_at` descending.

- **Reprocess**: `POST /api/library/entries/{id}/reprocess` (optional body `{ "profile_id": <id> }`).

- **Remove original**: `POST /api/library/entries/{id}/remove-original`.

## Events (from watcher)

- **Batch/Single**: `POST /api/events`

  ```json
  {
    "events": [
      {
        "path": "/watch/movies/demo.mkv",
        "library": "movies",
        "event": "created",
        "is_directory": false,
        "size": 12345,
        "modified_at": "2025-11-24T12:00:00Z"
      }
    ]
  }
  ```

## Jobs

- **List**: `GET /api/jobs` returns recent queue entries (newest first) with normalized paths and an `elapsed_seconds` field suitable for showing runtime in the dashboard.
- **Next job**: `GET /api/jobs/next`
- **Update status**: `POST /api/jobs/{job_id}/status` with `{ "status": "running|completed|failed", "progress": 0-100, "message": "..." }`
- **Acknowledge**: `POST /api/jobs/{job_id}/ack` with `{ "delivery_id": "..." }`
- **Clear processed jobs**: `POST /api/jobs/clear` removes completed and failed jobs from Redis.

## WebSocket

- **Endpoint**: `GET /ws`
- **Message types**:
  - `{"type": "job-update", "job": { ... }, "event": "status|queued|acquired|reprocess"}`
  - `{"type": "entry-update", "entry": { ... }, "event": "queued|tracked|job-status|reprocess|remove-original"}`
  - `{"type": "library-update", "action": "created|deleted", "library": { ... }}`

Clients should reconnect on close; dashboard already retries automatically.

## Queue and health

- `GET /api/queue/state`
- `POST /api/queue/pause` `{ "reason": "maintenance" }`
- `POST /api/queue/resume`
- `GET /api/healthz`, `GET /api/readyz`

## Logs

- `GET /api/logs?min_severity=INFO&source=&category=&query=`
- `POST /api/logs/ingest` for structured log batches.

## Config & profiles

- `GET /api/config`
- `POST /api/config/encoding` to upsert a profile (chromecast-safe validation enforced)
- `GET/POST/PUT/DELETE /api/profiles`

---

For UI/usage walkthroughs, see `docs/user/01_getting_started.md` and `docs/user/02_configuration.md`.
