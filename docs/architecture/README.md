# Architecture Overview

## Goals and constraints

- Watch bind-mounted `movies`/`series` libraries (typically Windows paths surfaced through Docker Desktop/WSL2) and keep every title Chromecast Gen 2/3 compliant.
- Enforce H.264 High profile video + AAC stereo audio with Chromecast-safe resolution/FPS/bitrate caps that the GPU worker validates before accepting an edit.
- Keep orchestration logic in Python (FastAPI + Redis queue) with SQLite-backed configuration, library catalog, job history, and log store files mounted on the host for persistence.
- Avoid silent CPU fallbacks: NVENC is always the first pipeline, and any CPU stage is only used when the retry pipeline explicitly drops to CPU.

## Container topology

| Service | Responsibility | Notes |
| --- | --- | --- |
| `orchestrator` | FastAPI app serving the dashboard, REST/WebSocket APIs, config editing, log ingestion, job history, and Redis-backed job coordination. | Stores config (`config.db`), library catalog (`library.db`), and logs (`events.db`) under `./data`. |
| `folder-watcher` | Python watchdog client that tails bind mounts, batches events, retries on failure, and replays a JSONL spool file when the API was unreachable. | Uses `/api/events` and honors `WATCH_POLLING`, `EVENT_BUFFER_SECONDS`, and spool-related env vars. |
| `gpu-ffmpeg` | CUDA-enabled ffmpeg worker. Polls `/api/jobs/next`, runs ffprobe/ffmpeg locally, posts status/ack events, manages subtitle sidecars, and optionally deletes originals after validation. | Loads profiles/operational settings from `/api/config`, emits telemetry/logs back to the orchestrator, and enforces NVENC-first pipelines. |
| `redis` | Durable queue for jobs plus reservation keys per source path. | Orchestrator owns visibility timeouts, pause/resume flags, and queue depth metrics. |

All containers share the `PATH_MOVIES`/`PATH_SERIES` mounts twice (`/watch/...` for watchers and `/media/...` for workers/orchestrator). NVIDIA Container Toolkit must be installed on the host so the worker can acquire `/dev/nvidia*`.

## Storage layout

- `config.db` — SQLite database that stores libraries, profiles, operational thresholds, log retention, and optional Jellyfin metadata. Seeds from `DEFAULT_CONFIG` inside `services/orchestrator/app/config.py`.
- `library.db` — Tracks one row per media path with current status, last job, decode/scale/encode pipeline, and whether the original still exists.
- `events.db` — Structured log store with severity/source/category indexes that power `/api/logs`, `/api/logs/*` metadata, and dashboard filtering.
- Redis keys — `job_queue` stream + consumer group per worker, job metadata hashes, and per-path reservation keys to dedupe scans and watcher events.

## Control and data flow

1. **Detection** — `folder-watcher` schedules OS-native observers (or `PollingObserver` when `WATCH_POLLING=true`). Events are grouped for `EVENT_BUFFER_SECONDS` seconds, posted to `/api/events`, and spooled to `EVENT_SPOOL_FILE` (default `/tmp/folder-watcher-spool.jsonl`) if HTTP delivery fails. On startup the watcher replays the spool in chunks of 50.
2. **Normalization** — `/api/events` resolves `/watch/...` and `/media/...` paths to a shared canonical form, discards directory events, and ignores non-media file extensions. `LibraryEntryStore` upserts entries while noting missing originals or outputs.
3. **Job creation** — `record_library_entry` checks for an existing `*-chromecast.mp4`. If conversion is required, `JobManager` enqueues a Redis job with the selected profile encoding payload. Manual scans via `/api/scan` walk the mounted root to reach the same logic.
4. **Worker loop** — `gpu-ffmpeg` calls `/api/jobs/next`, honoring `queue_state["paused"]`. Status callbacks hit `/api/jobs/{id}/status`; failing attempts classify stderr and may schedule retries with progressively more CPU stages. Once complete the worker calls `/api/jobs/{id}/ack`.
5. **Catalog updates** — Any status transition calls `sync_entry_from_job`, which updates the SQLite entry, attaches pipeline metadata, sets `output_path`, and posts `entry-update`/`job-update` messages over the WebSocket hub (`/ws`).
6. **Manual controls** — The dashboard surfaces queue pause/resume, `POST /api/jobs/clear`, `POST /api/jobs/purge-inactive`, library add/remove/profile reassignment, log retention updates, and ad-hoc rescans. Job history is fetched from `/api/history`.
7. **Jellyfin hooks** — When `jellyfin` config is present, startup triggers `/Library/Refresh` for each configured library ID without blocking the rest of the boot.

## GPU worker pipeline

- ffprobe runs with a 120 s timeout (`GPU_FFPROBE_TIMEOUT` override) and annotates streams with derived bit depth + HDR hints. The worker calculates duration to validate outputs or decide whether a file is still being written.
- `FFmpegBuilder` selects decode/scale/encode stages (NVDEC/NPP/NVENC first) and toggles HDR tonemapping (`tonemap_cuda` when supported, CPU filters otherwise). Bitrate/level/profile constraints are validated in orchestrator config before workers ever see them.
- Subtitle streams are extracted to `.srt` sidecars per language/index when ffmpeg can convert the codec; they are excluded from the MP4 mux so the GPU filter graph can stay on-device.
- Operational settings include `remove_original_after_success` (worker removes the source after validating duration/size). Audio/subtitle selection currently prefers Swedish first, then English, matching the hard-coded defaults in `FFmpegBuilder`.
- Retries follow `jobs.PIPELINE_SEQUENCE`, gradually dropping decode/scale/encode stages to CPU if NVENC keeps failing. Once the max attempts is exhausted the job status is marked `failed` with the classification result stored in `entry.last_error`.

## Observability

- `/api/logs`, `/api/logs/categories`, `/api/logs/sources`, and `/api/logs/stats` read from `events.db`. Every service ships a custom logging handler that forwards structured entries (timestamp, severity, source, category, logger, request id).
- `/api/metrics` summarizes job counts plus worker telemetry (`gpu_available`, NVENC filter support, ffmpeg build info) stored via `/api/workers/telemetry`. The dashboard mirrors the same status pills (`GPU: X/Y ready`, queue depth, pause reason).
- `/api/history` lists the most recent job completions/failures so operators can audit runtimes and failure reasons.
- Health endpoints: `/api/healthz` reports whether libraries are loaded, `/api/readyz` flips ready once the FastAPI stack is up.

## Recovery characteristics

- Watcher spool ensures no filesystem event is lost if the API was down. Spool trimming is manual today; operators can delete the spool after a successful replay.
- Redis-based job reservations avoid double-encoding by deduping entries on the canonical path key. Manual rescans reuse the same keys, so reprocessing is idempotent as long as `-chromecast` outputs remain newer than the source.
- `LIBRARY_STORE.mark_missing` marks entries as `removed` when a scan no longer sees them, letting the dashboard flag missing originals even before workers pick up new jobs.
- Manual tools exist for every critical step: `POST /api/library/entries/{id}/reprocess` requeues failures, `.../remove-original` deletes validated sources, and queue purge/clear commands recover from stale Redis deliveries without shell access.

Docker Compose keeps the services loosely coupled—each container can be rebuilt or restarted independently while SQLite/Redis maintain continuity.
