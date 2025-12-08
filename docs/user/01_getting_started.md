# Getting Started

This guide mirrors the behavior implemented in `docker-compose.yml`, the FastAPI routers, and the watcher/worker services. Follow it to bring the stack online on a GPU-capable host.

## Prerequisites

- Docker Engine or Docker Desktop with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/index.html) so the `gpu-ffmpeg` container can reach NVENC devices.
- A host directory that contains your media (for example `D:\Media\Movies` on Windows) and can be bind-mounted twice (`/watch/...` for watchers and `/media/...` for encoders/orchestrator).
- Open access to `localhost:9000` for the dashboard/API and `localhost:6379` if you need to inspect Redis.

## Prepare the environment

1. Copy `.env.template` to `.env`.
2. Set `PATH_MOVIES` and `PATH_SERIES` to the host paths that should be mounted into the containers. Relative entries are resolved against the repository root, e.g. `./media/movies`.
3. Optional overrides:
   - `GPU_STREAM_READER_LIMIT`, `GPU_FFPROBE_TIMEOUT`, `GPU_FFMPEG_TIMEOUT`, `GPU_FFMPEG_IDLE_TIMEOUT`, and `GPU_SUBTITLE_TIMEOUT` control the worker timeouts exposed in `services/gpu-ffmpeg/app/worker.py`.
   - `WATCH_POLLING`, `EVENT_BUFFER_SECONDS`, `EVENT_RETRY_ATTEMPTS`, `EVENT_RETRY_BACKOFF_SECONDS`, and `EVENT_SPOOL_FILE` control how the watcher batches events and how much backlog it retains when the API is unavailable.
   - `JOB_VISIBILITY_TIMEOUT` (seconds) governs how long a worker can hold a job before Redis re-delivers it.

Configuration is stored in SQLite (`./data/config.db`). The orchestrator seeds that file with the defaults from `services/orchestrator/app/config.py` the first time it boots (two libraries + one `chromecast` profile). All runtime changes should go through the dashboard or API; editing the DB manually is unsupported.

## Build and run

```bash
docker compose build
docker compose up
```

- `orchestrator` exposes the dashboard/API on `http://localhost:9000`.
- `folder-watcher` waits until the bind mounts exist, then streams file events to `/api/events`. If the API is unreachable it appends batches to the spool (`/tmp/folder-watcher-spool.jsonl`) and replays the file on the next start.
- `gpu-ffmpeg` polls `/api/jobs/next`, fetches `/api/config` for the latest profiles/operational settings, emits telemetry via `/api/workers/telemetry`, and forwards structured logs to `/api/logs/ingest`.
- `redis` holds the job stream, queue depth counter, and per-path dedupe keys.

Stop the stack with `Ctrl+C` or `docker compose down`. Removing the `redis_data` volume resets queued jobs.

## Dashboard tour

The SPA served from `/` mirrors the FastAPI routers under `services/orchestrator/app/routers`:

- **Queue management** — Trigger manual scans (`/api/scan`), pause or resume intake, clear processed jobs, purge inactive deliveries, and monitor depth (accurate real-time Redis stream count) + GPU readiness.
- **Job history** — Lists the most recent entries from `JOB_HISTORY_STORE`, including elapsed runtime and failure messages.
- **Library entries** — Filters the SQLite catalog by library or status. Features "Reprocess All" and "Delete All Originals" bulk actions alongside individual entry controls.
- **Logs** — Reads `/api/logs`, `/api/logs/categories`, `/api/logs/sources`, and `/api/logs/stats`.
- **Configuration** — Edits encoding profiles (GPU/CPU-specific knobs like NVENC preset, VBR/CRF, bitrate), manages operational settings (scan interval), and log retention.

The dashboard holds an open WebSocket (`/ws`) that relays `job-update`, `entry-update`, and `library-update` payloads broadcast by the orchestrator, so status changes land immediately without polling.

## Operational tips

- Use `/api/jobs/clear` when the queue table accumulates completed/failed rows or to free Redis memory.
- `POST /api/jobs/purge-inactive` removes deliveries that never transitioned to `running` (e.g., when a worker crashed before acking).
- When a file is re-encoded manually (`POST /api/library/entries/{id}/reprocess`), the orchestrator reserves that path before scheduling a new job to avoid duplicate conversions.
- `/api/library/entries/{id}/remove-original` deletes the source only when the `*-chromecast.mp4` output exists and has a non-zero size; the GPU worker mirrored logic when `remove_original_after_success` is enabled.

## Troubleshooting

- `GET /api/healthz` verifies that libraries are loaded; `GET /api/readyz` simply confirms the FastAPI app is alive.
- Use `docker compose logs -f service-name` for container-level issues. All containers also forward logs into `events.db`, so the dashboard log view should mirror the same output.
- If watcher mounts are slow to appear (common on WSL2), it keeps retrying every 5 seconds before scheduling observers. You can change this behavior with `ROOT_RETRY_SECONDS` inside the Compose file.
- Delete `./data/*.db` only when you intentionally want to reset configuration, library catalog, and logs—doing so wipes runtime state.
