# 01 - Getting Started

This guide walks through prerequisites, configuration, and day-one operation of the Chromecast Video Converter MVP. The stack is intended for local GPU-equipped hosts and runs entirely via Docker Compose.

## Prerequisites

- Docker Desktop or a compatible Docker Engine installation.
- NVIDIA GPU with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/index.html) installed so the `gpu-ffmpeg` service can reach NVENC devices.
- Open network access to `localhost:9000` for the dashboard/API and `localhost:6379` if you want to inspect Redis directly.

## Initial setup

1. (Optional) Copy `config/settings.yaml.template` to `config/settings.yaml` and adjust library roots or profiles before the first boot. On startup the orchestrator imports `settings.yaml` into the SQLite config store at `./logs/config.db` (falling back to the template when no overrides exist) and validates it before use.
2. Keep the left-hand side of the Compose volume mounts aligned with your host paths and use the corresponding `/watch/...` or `/media/...` paths inside the YAML seed so the imported defaults match the container mounts.
3. Review operational guardrails in the seed (GPU temperature cutoff, disk usage limits, and whether originals are deleted after successful verification); after boot, update future changes through the dashboard so they persist in the database.
4. Build the stack locally:
   ```bash
   docker compose build
   ```
5. Start the services:
   ```bash
   docker compose up
   ```

## Using the dashboard and API

- Open `http://localhost:9000` to view the dashboard. It surfaces queue counts, recent logs, and manual scan controls.
- Health endpoints: `/api/healthz` (confirms libraries are loaded) and `/api/readyz` (signals the API is ready to serve jobs).
- GPU telemetry: the queue header reports how many workers have usable CUDA/NVENC
  devices (for example, `GPU: 1/1 ready`). When no encoder is detected the
  worker returns an explicit error and the job fails fast instead of falling
  back to CPU encoding.
- Queue controls: `/api/queue/pause` and `/api/queue/resume` allow operators to throttle work when storage or thermal limits are reached.
- Logging: `/api/logs` returns recent log entries across the orchestrator, GPU workers, and folder watcher. Configure the retention window (default 7 days) and review log disk usage from the Configuration page.
- Library management: add libraries at runtime with `POST /api/libraries` (fields: `name`, `root`, `profile_id`) or the Configuration page form. The orchestrator always scans an entire tree (`depth` defaults to `"max"`), so the UI hides that field and normalizes `/watch/...` inputs to `/media/...` for clarity. Remove libraries with `DELETE /api/libraries/{name}`; existing entries are marked `removed` for traceability.
- Live updates: the dashboard keeps a WebSocket open to `/ws` so job and entry updates land in real time. Connections auto-retry if the API restarts.
- Job lifecycle:
  - `/api/scan` triggers a (re)scan of configured libraries to enqueue work.
  - `/api/jobs/next` supplies the next job to GPU workers.
  - `/api/jobs/{id}/status` records progress and completion updates from workers.
  - `/api/library/entries` now accepts `limit`, `offset`, and `include_total` for paginated browsing; the dashboard uses a “Load more” control instead of refetching the entire catalog on every refresh.
  - `/api/jobs/clear` removes completed/failed jobs from Redis so the dashboard’s queue table stays focused on active work (the **Clear processed items** button on the Queue page calls this endpoint and refreshes automatically).

## Media watcher behavior

The `folder-watcher` container uses `inotifywait` to stream create/modify/delete events from the mounted `movies` and `series` directories. Events include the library name, full path, basic metadata, and whether the entry is a directory. The watcher backs off and retries when the orchestrator API is temporarily unavailable and can optionally buffer events for batch delivery. Set `EVENT_BUFFER_SECONDS` to a non-zero value in `docker-compose.yml` to group events into timed batches; adjust `EVENT_RETRY_ATTEMPTS` and `EVENT_RETRY_BACKOFF_SECONDS` to control the retry window if the API is down. If the API remains unreachable, undelivered batches are spooled to `EVENT_SPOOL_FILE` (default `/tmp/folder-watcher-spool.jsonl`) and replayed automatically on the next start.

## Cleanup and troubleshooting

- Stop the stack with `Ctrl+C` in the Compose terminal or `docker compose down` from another shell.
- If you adjust volume mounts or the quality config, restart the stack so new paths and guardrails take effect.
- Redis data persists in the `redis_data` volume; remove it with `docker volume rm chromecast-video-converter_redis_data` if you want a clean queue state.
