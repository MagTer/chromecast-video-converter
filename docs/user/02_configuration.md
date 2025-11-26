# 02 - Configuration Guidelines

## Media paths via `.env`

- Copy `.env.template` to `.env` and set `PATH_MOVIES`/`PATH_SERIES` to the host directories that hold your libraries. Relative values are resolved from the repository root (for example, `./media/movies`); absolute paths work for network shares or mounted drives such as `/mnt/storage/Movies` or `D:\\Media\\Movies` on Windows.
- Docker Compose consumes those variables in every service, binding each host directory twice: once to `/watch/<library>` and once to `/media/<library>`. The orchestrator understands both mount roots, so UI/API calls and watcher events can reference either prefix.
- Because these host-root bindings determine what the containers actually see, the orchestrator’s library definitions stored in the config database must use one of the mounted Linux paths (`/watch/movies`, `/watch/series`, `/media/...`) while the Windows host path stays locked to the left-hand side of the Compose mounts.
- Optional worker overrides can also live in `.env`. Set `GPU_STREAM_READER_LIMIT` if long FFmpeg stderr lines trigger `LimitOverrunError` during encoding; Compose forwards that value to the GPU worker (default: `1000000`).

## Watcher configuration flags

- `WATCH_ROOTS` pairs library names with absolute paths inside the container (e.g., `movies:/watch/movies,series:/watch/series`).
- `EVENT_BUFFER_SECONDS` controls optional batching of inotify events before they are posted to the orchestrator. Set to `0` to send immediately or a positive integer to flush on that cadence.
- `EVENT_RETRY_ATTEMPTS` and `EVENT_RETRY_BACKOFF_SECONDS` define the retry window when the orchestrator API is temporarily unavailable. Retries use exponential backoff based on the provided delay.
- `ROOT_RETRY_SECONDS` governs how frequently the watcher waits for a missing mount to appear before starting the inotify loop.
- `EVENT_SPOOL_FILE` (default: `/tmp/folder-watcher-spool.jsonl`) persists undelivered batches when the orchestrator is offline; buffered payloads are replayed on the next start before new inotify events are processed. `EVENT_SPOOL_MAX_BYTES` caps the retained backlog (defaults to 10 MB) to prevent unbounded growth.

`config/settings.yaml.template` is solely a starter copy that seeds the SQLite config store (`./logs/config.db`). If `config/settings.yaml` exists on first boot, the orchestrator imports it once, validates it, and ignores the YAML copies afterward. The GPU worker now pulls settings from the orchestrator API, so ongoing edits should happen through the dashboard/API rather than direct file edits.

The Compose stack still mounts `./config` into both the orchestrator and GPU worker so the template and any legacy overrides remain available for seeding, but runtime state persists in the database under `CONFIG_DB_PATH` (`./logs/config.db` by default).

## GUI-powered tuning

- The orchestrator dashboard & API accept JSON/YAML that controls library names, profiles, bitrates, and Jellyfin integration. Those fields are surfaced through the GUI so operators can tune quality and automation; they do not change the host path mappings.
- The Queue page now highlights active work more clearly: job IDs are clickable shortcuts that pre-fill the log search box, an **Elapsed** column shows how long each job has been running or how long it took, and a **Clear processed items** button issues `POST /api/jobs/clear` to remove completed/failed jobs from Redis.
- Encoding controls are GPU-first and NVENC-only: presets remain P4–P7 but the UI defaults to P7 for maximum quality, rate control choices are now limited to CQ or single-pass VBR (VBR HQ was removed to avoid multipass driver issues), and defaults favor 720p30 at 5 Mbps target / 10 Mbps max with a 16 Mbps buffer. CQ vs. bitrate/maxrate/bufsize inputs auto-enable based on the RC mode, B‑frames still honor profile constraints (0 when baseline), lookahead stays 0–32 with adaptive B-frames gated on lookahead>0, and AQ exposes on/off toggles plus a new strength slider (5–10, default 7) so you can bias how aggressively bits migrate to complex scenes. Level constraints auto-adjust when a chosen resolution/FPS requires 4.0/4.1/4.2. Audio remains AAC stereo (2 channels) with selectable bitrates.
- The GUI blocks invalid H.264 level, resolution, and FPS mixes as you edit: picking 1080p60 automatically bumps the level to 4.2 and disables lower levels; dropping the level to 3.1 constrains the menus to 720p30/60-safe choices. Inline tooltips (ⓘ) explain rate control, presets, B‑frames, lookahead, and AQ in plain language.
- The Configuration page includes an **Add library** form. Provide a unique name, a mounted path such as `/media/movies` (the UI will normalize any `/watch/...` inputs to `/media/...` for display), and an existing encoding profile. The orchestrator always performs a full recursive scan (`depth` defaults to `max`), so there is no longer a depth input to juggle. On save, the orchestrator persists the definition, kicks off a background scan, and the new path becomes available immediately without restarting containers.
- Library removal is supported directly from the dashboard or via `DELETE /api/libraries/{name}`. Removing a library marks its existing entries as `removed` in the catalog (for auditability) but leaves the historical rows intact.
- When adding libraries, ensure the `root` matches a mounted path; otherwise files will not be reachable.
- Jellyfin integration is optional; omit the `jellyfin` section from the seed (as shown in `config/settings.yaml.template`) whenever no server is reachable, and the orchestrator will quietly skip those refresh tasks.
- Log retention is also editable in the GUI. The `logging.retention_days` field is stored in the config database (default: `7`) and controls how long centralized logs from every container stay on disk. The Configuration page displays current disk usage for the log database mounted at `./logs`.
- Log records include `severity`, `source`, and `category` metadata in the `/api/logs` payloads, and the dashboard filters default to `INFO` and above to suppress verbose chatter. Keep orchestrator, watcher, and worker containers at `LOG_LEVEL=INFO` during normal operation; only switch to `VERBOSE` temporarily when debugging and rely on the UI filters to surface warnings and errors quickly.

## Live updates and pagination

- The dashboard subscribes to the orchestrator WebSocket at `/ws` for job status, library entry, and library add/remove events. No manual refresh is required to see queue/entry changes; connections will auto-retry if interrupted.
- The **Library entries** view loads results in pages (`limit`/`offset`) with a “Load more” control. The API supports `include_total=true` on `/api/library/entries` to return `{items, total, limit, offset}` so clients can decide when to stop fetching.
- Example paginated request:

  ```bash
  curl "http://localhost:9000/api/library/entries?limit=50&offset=0&include_total=true&status=pending"
  ```

## Keeping configs aligned

- After editing `docker-compose.yml` to point to different host folders, restart the stack to refresh the mounts.
- If you add new directories in Compose later, update the Configuration page (or reseed `config/settings.yaml` and restart) to include the new library/profile pair so the orchestrator knows how to schedule jobs there.
