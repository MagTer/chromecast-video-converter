# Configuration & Runtime Controls

Everything in this guide maps directly to code under `services/orchestrator/app`, `services/folder-watcher/app`, and `services/gpu-ffmpeg/app`. Use it as the single source of truth for what the stack actually supports.

## Media paths and `.env`

- Copy `.env.template` to `.env` and set `PATH_MOVIES` / `PATH_SERIES` to host directories.
- Compose mounts each path twice:
  - `/watch/<name>` — read-only mount that the watcher tails.
  - `/media/<name>` — read-only mount used by the orchestrator (library scans, log links) and the GPU worker (ffprobe/ffmpeg inputs and outputs).
- The orchestrator normalizes both prefixes via `resolve_media_path`, so API payloads and watcher events can use either prefix interchangeably. When responding, the API favors the `/media` prefix.

## Library and profile management

The config database (`config.db`) stores all runtime settings:

- `POST /api/libraries` accepts `{name, root, profile_id}`. The server trims empty names, enforces uniqueness, normalizes paths, forces `depth="max"`, and kicks off `reconcile_library` in the background.
- `PATCH /api/libraries/{name}` switches to a different profile. Only profile IDs that exist in `PROFILE_STORE` are accepted.
- `DELETE /api/libraries/{name}` removes the definition and marks every tracked entry for that library as `removed`.
- `POST /api/scan` runs a manual walk for either a specific library (`{"library":"movies"}`) or all libraries (empty body). Scans reuse the same dedupe logic as watcher events.

Profiles are edited via `/api/config/encoding` (or `/api/profiles` CRUD endpoints). FastAPI re-validates every field through `HardwareProfile` so only Chromecast-safe resolutions, FPS, bitrates, and AAC stereo settings can be saved. Each profile stores both GPU (NVENC) and CPU (fallback) blocks. CPU settings are currently only used when the retry pipeline falls through to CPU stages after an NVENC failure.

## Queue controls

- `POST /api/queue/pause` and `/api/queue/resume` toggle the Redis flag the worker checks before dequeuing.
- `POST /api/jobs/clear` deletes completed/failed jobs from Redis. `POST /api/jobs/purge-inactive` removes deliveries that never advanced to `running`.
- `/api/queue/state` returns the current pause reason, queue depth, and worker telemetry summary.

Job state changes always follow this sequence:

1. Worker acquires a job via `/api/jobs/next`.
2. `sync_entry_from_job` updates the library row to `converting`.
3. Worker pushes `/api/jobs/{id}/status` events during the run (`running`, `completed`, or `failed`).
4. `record_job_history` persists each transition; `/api/history` lists the most recent entries.
5. Worker acknowledges the delivery with `/api/jobs/{id}/ack`.

Retries are automatic when `classify_ffmpeg_error` labels a failure as retryable and the job has remaining attempts. Pipelines progress from all-GPU to CPU decode/scale/encode if necessary.

## Library catalog & pagination

- `/api/library/entries` accepts `limit`, `offset`, `status`, and `library`.
- The endpoint returns a plain array of entries sorted by `updated_at` (no total count). The dashboard detects additional pages by comparing `items.length` to the requested limit.
- Each entry includes the normalized path, output path, status, last job ID, decode/scale/encode types, and `original_missing` flag.
- `POST /api/library/entries/{id}/reprocess` requeues a job. The orchestrator rechecks whether the source still exists before enqueuing.
- `POST /api/library/entries/reprocess-all` requeues all eligible entries for reprocessing.
- `POST /api/library/entries/{id}/remove-original` deletes the source only when the converted output is present and non-zero. Errors return HTTP 409 with a descriptive message.
- `POST /api/library/entries/delete-all-originals` deletes source files for all entries where a successful conversion exists.

## Operational settings

The **Operational settings** section in the configuration page allows tuning background tasks:

- **Scan interval**: Defines how frequently (in minutes) the system triggers a full library scan. Setting this to `0` disables scheduled scanning. Changes are picked up dynamically by the folder watcher service.

## Folder watcher knobs

Environment variables are read at import time inside `services/folder-watcher/app/watcher.py`. Note that the scan interval is now primarily controlled via the API (see above), though other behaviors remain env-var driven:

| Variable | Default | Purpose |
| --- | --- | --- |
| `WATCH_ROOTS` | `movies:/watch/movies,series:/watch/series` | Comma-separated `<library>:<path>` list. Paths must match the container mounts. |
| `WATCH_POLLING` | `false` | When `true`, uses `PollingObserver` with `POLLING_INTERVAL` seconds between scans (better for WSL2/OSX bind mounts). |
| `EVENT_BUFFER_SECONDS` | `1` | Buffer window before sending collected events. `0` flushes immediately. |
| `EVENT_RETRY_ATTEMPTS` / `EVENT_RETRY_BACKOFF_SECONDS` | `5` / `2` | Exponential backoff strategy for POST `/api/events`. |
| `EVENT_SPOOL_FILE` | `/tmp/folder-watcher-spool.jsonl` | JSONL spool used whenever HTTP posting fails after retries. Replayed automatically on restart. |
| `EVENT_SPOOL_MAX_BYTES` | `10485760` | Emits a warning when the spool size exceeds this number of bytes. |

Each queued event includes the library, absolute path, event type (`created`, `modified`, `deleted`), and optional size/timestamp metadata.

## GPU worker configuration

Refer to `services/gpu-ffmpeg/app/worker.py` for authoritative behavior:

- `LOG_LEVEL` / `FFMPEG_LOG_LEVEL` adjust orchestrator-ingested logs and ffmpeg verbosity.
- `GPU_POLL_INTERVAL` (default `5s`) controls how often the worker polls for new jobs.
- `GPU_FFPROBE_TIMEOUT`, `GPU_FFMPEG_TIMEOUT`, `GPU_FFMPEG_IDLE_TIMEOUT`, and `GPU_SUBTITLE_TIMEOUT` cap ffprobe, full encoding, no-progress windows, and per-subtitle extraction respectively (`0` disables a limit).
- `JOB_VISIBILITY_TIMEOUT` must be at least as long as the longest expected encode so Redis does not redeliver an active job.
- `ORCHESTRATOR_URL` and `WORKER_ID` identify the worker in telemetry payloads and logs.
- `remove_original_after_success` is read from the `operational` block inside `/api/config`. When `true`, the worker deletes the source only after validating the output duration/size. Library-level removal via the API remains independent of this flag.
- Language selection currently favors Swedish audio/subtitles, then English. The preference list lives in `FFmpegBuilder.DEFAULT_LANGUAGE_PREFERENCES`.

Workers stream telemetry to `/api/workers/telemetry` (GPU names, NVENC capability flags, ffmpeg filter availability). The queue view consumes this payload to render the `GPU: X/Y ready` pill.

## Logging & retention

- Every service installs an `OrchestratorLogHandler` that POSTs to `/api/logs/ingest`. The payload captures timestamp, severity (with INFO/WARNING/ERROR plus `VERBOSE` for debug logs), source, category, logger name, message, and optional `request_id`.
- `/api/logs` filters by `min_severity`, `source`, `category`, `logger`, or free-text `query` across the stored rows. Results are capped at 200 entries per call.
- `/api/config/logging` updates the retention window. The handler immediately updates the SQLite store (`LogStore.update_retention`) and prunes entries older than `retention_days`.
- `/api/logs/stats` reports how many rows are stored and the configured retention so operators know when to compact.

## Live updates

The `WebsocketNotifier` broadcasts the following payloads to every `/ws` client:

- `{"type": "job-update", "job": {...}}`
- `{"type": "entry-update", "entry": {...}}`
- `{"type": "library-update", "action": "created|deleted", "library": {...}}`

Because the socket requires clients to send heartbeat frames, the dashboard keeps the connection alive by sending empty messages on an interval and will reconnect if the socket closes.
