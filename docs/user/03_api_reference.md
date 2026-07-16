# API Reference

All endpoints live under the orchestrator base URL (default `http://localhost:9000`). The list below mirrors the actual routers and return shapes inside `services/orchestrator/app/routers`.

## Libraries

| Action | Method & Path | Notes |
| --- | --- | --- |
| List libraries | `GET /api/libraries` | Returns an array of `{name, root, depth, profile_id, profile}`. |
| Create library | `POST /api/libraries` | Body `{ "name": "...", "root": "/media/movies", "profile_id": 1 }`. Depth is forced to `"max"`; duplicate names return `409`. |
| Update profile | `PATCH /api/libraries/{name}` | Body `{ "profile_id": 2 }`. |
| Delete library | `DELETE /api/libraries/{name}` | Marks existing entries from that library as `removed` in addition to deleting the config row. |
| Manual scan | `POST /api/scan` | Optional body `{ "library": "movies", "root": "/watch/movies" }`. Missing `library` scans all definitions. |

## Library entries

| Action | Method & Path | Notes |
| --- | --- | --- |
| List entries | `GET /api/library/entries?limit=100&offset=0&status=&library=&compliance=&query=&include_total=false` | Returns an array by default; with `include_total=true` it returns `{ "items": [...], "total": N, "limit": L, "offset": O }`. `compliance` filters on the Chromecast verdict (`compliant`, `noncompliant`, `unverified`); `query` is a case-insensitive substring match on path or library. Paths/output paths are normalized to `/media/...`. Entries include `output_compliant` (bool/null) and a parsed `compliance` object (issues, video summary). |
| Entry summary | `GET /api/library/entries/summary?library=` | Whole-table counts per status plus `noncompliant` and `total`. Feeds the dashboard summary cards. |
| Reprocess entry | `POST /api/library/entries/{id}/reprocess` | Optional body `{ "profile_id": <int> }`. Fails with `404` if the entry or profile does not exist. Queuing a reconversion clears any stored compliance verdict. |
| Verify entry | `POST /api/library/entries/{id}/verify` | Queues a lightweight ffprobe verification of the converted output (no GPU). `409` if the entry has no converted output. |
| Verify all | `POST /api/library/entries/verify-all` | Queues verification for every `converted`/`removed` entry. Returns `{ "queued_count": N }` — an upper bound, since already-queued duplicates are skipped in Redis. |
| Reprocess all | `POST /api/library/entries/reprocess-all` | Queues a forced reconversion for every entry whose original still exists. |
| Delete all originals | `POST /api/library/entries/delete-all-originals` | Queues delete jobs for originals whose converted output exists and is non-empty. |
| Change entry profile | `PATCH /api/library/entries/{id}` | Body `{ "profile_id": <int> }` rewrites the stored profile without scheduling a job. |
| Remove original | `POST /api/library/entries/{id}/remove-original` | Deletes the source file once the converted output is verified to exist. Returns `409` if the output is missing or empty. |

## Watcher events

```
POST /api/events
{
  "events": [
    {
      "path": "/watch/movies/demo.mkv",
      "library": "movies",
      "event": "created|modified|deleted",
      "is_directory": false,
      "size": 123456,
      "modified_at": "2025-12-01T18:04:00Z"
    }
  ]
}
```

Events are processed in order. Non-media files are ignored; delete events immediately mark entries `removed`.

## Jobs & queue

| Action | Method & Path | Notes |
| --- | --- | --- |
| List jobs | `GET /api/jobs` | Array of queued/active jobs (newest first) including `elapsed_seconds` and pipeline info. |
| Claim job | `GET /api/jobs/next?worker_id=worker-1` | Returns a payload with job data and `delivery_id`. Responds `204` when nothing is available or `409` if the queue is paused. |
| Update status | `POST /api/jobs/{id}/status` | Body includes `status` (`running`, `completed`, `failed`), optional `progress`, `message`, `return_code`, `logs`, the effective `pipeline`, and an optional `compliance` verdict (`{ "compliant": bool, "issues": [...], "video": {...} }`). Failed convert jobs trigger automatic retries unless the classification is non-retryable; `verify` jobs never alter entry status and never retry. |
| Acknowledge delivery | `POST /api/jobs/{id}/ack` | Body `{ "delivery_id": "..." }`. Required after a worker finishes handling a job. |
| Clear processed | `POST /api/jobs/clear` | Removes `completed`/`failed` jobs from Redis. |
| Purge inactive | `POST /api/jobs/purge-inactive` | Sweeps jobs that never transitioned to `running`. Useful after hard worker crashes. |
| Queue state | `GET /api/queue/state` | Returns pause status, reason, depth, and worker telemetry summary. |
| Pause queue | `POST /api/queue/pause` `{ "reason": "maintenance" }` |
| Resume queue | `POST /api/queue/resume` |

## Job history

`GET /api/history?limit=100` lists the SQLite-backed history entries in reverse chronological order (`id`, `path`, `library`, `profile`, `status`, `job_type` (`convert`/`verify`/`delete`), `message`, `created_at`, `updated_at`, `elapsed_seconds`).

## Logs

- `GET /api/logs?min_severity=INFO&source=&category=&query=` returns up to 200 structured entries.
- `GET /api/logs/categories` and `/api/logs/sources` provide dropdown data for the dashboard filters.
- `GET /api/logs/stats` shows how many entries are stored and the retention window.
- `POST /api/logs/ingest` accepts `{ "entries": [ { "timestamp": "...", "level": "INFO", "severity": "INFO", "logger": "...", "source": "...", "category": "...", "message": "..." } ] }`. Missing `source`/`category` are derived from the logger name.

## Configuration

| Action | Method & Path | Notes |
| --- | --- | --- |
| Get config snapshot | `GET /api/config` | Returns sanitized config including libraries, profile definitions, operational settings, log retention, optional Jellyfin block, and `environment.is_wsl2`. |
| Upsert encoding profile | `POST /api/config/encoding` | Body matches `EncodingUpdatePayload` (one GPU block + one CPU block). |
| CRUD profiles | `GET/POST/PUT/DELETE /api/profiles[/{id}]` | Create/delete individual profiles without touching the library mappings. Delete returns `204` on success. |
| Update log retention | `POST /api/config/logging` `{ "retention_days": 7 }` |
| Update scan interval | `POST /api/config/operational` `{ "scan_interval_min": 30 }` | `0` disables scheduled scans. |
| Reset config | `POST /api/config/reset` | Reverts to `DEFAULT_CONFIG`. Profiles already stored in `PROFILE_STORE` remain so existing IDs continue to match queued jobs. |

## WebSocket & telemetry

- `GET /ws` — multiplexed channel for `job-update`, `entry-update`, and `library-update` payloads. Clients should echo small keep-alive messages and reconnect on close.
- `POST /api/workers/telemetry` — GPU workers post `{ "worker_id": "...", "hostname": "...", "gpu_available": true, "ffmpeg_version": "...", "tonemap_cuda": true, ... }`. The server keeps the latest payload per worker ID.
- `GET /api/metrics` — returns `{ "jobs": { "pending": N, ... }, "workers": { "workers": W, "available": A, "telemetry": [...] } }` for dashboards or Prometheus exporters.

## Health

- `GET /api/healthz` — `{"status":"ok","libraries":<count>}` once the orchestrator has loaded the config snapshot.
- `GET /api/readyz` — `{"status":"ready"}` as soon as FastAPI is listening.
