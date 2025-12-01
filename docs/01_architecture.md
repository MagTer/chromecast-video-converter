# 01 - Architecture Overview

## Goals and constraints

- Monitor separate `movies` and `series` libraries exposed from Windows (bind mounts via Docker Desktop/WSL2).
- Keep every asset streamable on Chromecast Gen 2/3 without server-side transcoding.
- Guarantee GPU-only encoding on an NVIDIA RTX 3060 and cap resolution at 720p.
- Prioritize perceptual quality and smooth action playback while targeting 1.8-3.2 GB movies.
- Enforce H.264 (High, auto-level from 3.1 upward) video / AAC 192 kbps stereo audio (2 channels), yuv420p pixel format, NVENC preset `p7` by default (P4–P7 allowed), and rate control modes CQ, single-pass VBR, or multi-pass VBR HQ (CQ keeps `-rc constqp -qp <cq>`, VBR uses `-rc vbr` plus `-b:v/-maxrate/-bufsize`, and VBR HQ layers on `-multipass fullres` when workers report support). We drive lookahead through FFmpeg 6.1’s `-rc-lookahead` flag (0–32 frames) with adaptive B-frames gated on lookahead>0, keep AQ on by default (`-spatial_aq 1 -temporal_aq 1`) with a tunable strength slider, always set `-movflags +faststart`, and only downscale (`scale=-2:720:force_original_aspect_ratio=decrease` with fps caps per profile).
- Deliver production-grade logging, guardrails for invalid configs, and fault tolerance.

## Container topology

| Container | Base | Role |
| --- | --- | --- |
| `orchestrator` | Ubuntu LTS | Coordinates workers, applies policy, exposes API/logging, persists state in SQLite. Primary configuration entrypoint and status dashboard. Supports runtime library add/remove, websocket broadcasts, paginated entry queries. |
| `folder-watcher` | Python + Watchdog | Watches bind-mounted `movies` and `series` folders, emits events to orchestrator via HTTP. Supports `WATCH_POLLING` for Docker Desktop compatibility. Spools undelivered events if API is down. |
| `gpu-ffmpeg` | Jellyfin base (CUDA-enabled FFmpeg) + Python worker | Executes validation and transcode jobs using NVENC. Pulls jobs via HTTP (`/api/jobs/next`) to ensure decoupling from the backing queue. |
| `queue` (optional) | Redis | Buffers work to smooth spikes. |

All containers join a private Docker network. Bind mounts provide the Windows-host media folders and a `config/` directory for persisted SQLite databases. NVIDIA Container Toolkit is required so `gpu-ffmpeg` can access the RTX 3060 from WSL2.

## Data flow

1. **Change detection** – `folder-watcher` monitors roots and posts create/modify/delete events to `/api/events`; if the API is down, events are written to the spool file and replayed on next start.
2. **Runtime config** – Libraries and profiles seed from the config DB; operators can **add/remove libraries at runtime** via `/api/libraries` or the dashboard, which triggers background scans and marks removed libraries’ entries as `removed`.
3. **Policy evaluation** – Orchestrator validates profiles/libraries from the SQLite config store (seeded from built-in defaults in `app/config.py`). Config changes remain Chromecast-safe (H.264 High with minimum level 3.1, AAC stereo, GPU-first with CPU fallback) and the API normalizes `/watch/...` mounts to `/media/...` when returning data.
4. **Job lifecycle** – Events and scans upsert library entries and enqueue jobs in Redis when needed. Workers pull `/api/jobs/next`, report progress via `/api/jobs/{id}/status`, and acknowledgements update catalog status/history.
5. **Live updates** – Orchestrator broadcasts `job-update`, `entry-update`, and `library-update` over `/ws`; the dashboard and any clients can subscribe instead of polling. Library entries are fetched with paginated `/api/library/entries` (`limit/offset/include_total`) and appended in the UI via “Load more.”
6. **Observability** – Structured logs persist in SQLite and expose `/api/logs`; metrics include queue depth and worker GPU availability. Telemetry and job history stay aligned with websocket pushes.

## User interface and manual controls

- A lightweight dashboard served from the orchestrator exposes health, queue metrics, and manual controls. The queue page shows job IDs (clickable to pre-fill the log search), elapsed runtime, normalized file names, and a **Clear processed items** button that calls `/api/jobs/clear` to prune completed/failed jobs. Operators can still trigger rescans via `/api/scan` before files are watched.
- The orchestrator also exposes `/api/events` for watchers or other adapters to notify about new media, plus `/api/jobs/{id}/status` so GPU workers can report progress.

## Jellyfin and optional integrations

- Optional Jellyfin integration can poll or receive webhooks from the local media server and call its `/Library/Refresh` API so our pipeline stays in sync with the catalog it already maintains.
- Jellyfin remains a trigger/metadata source; this stack keeps encoding independent so we can keep using GPU-only paths without inheriting Jellyfin’s transcoding engine.
- With that dependency in mind, we deliberately keep the worker as its own service even though it reuses Jellyfin’s FFmpeg stack. That separation of concerns makes it easy to add a second Jellyfin-based service later—one that exposes the media server/UI while the worker keeps Jellyfin disabled and purely drives FFmpeg tonemapping for the orchestrator.

## Fault tolerance and recovery

- **Idempotent jobs** - Each job references content by checksum, allowing safe retries.
- **Circuit breakers** - Orchestrator can pause scheduling if GPU temperature exceeds thresholds or storage free space is low.
- **Rollback strategy** - Originals persist until verification passes. Failures keep source files untouched and log detailed ffmpeg stderr for analysis.
- **Self-healing watchers** - `folder-watcher` restarts quickly (tiny Alpine image). If orchestrator is unavailable, watchers retry with exponential backoff and can buffer events locally before replaying.
- **Media gatekeeping** - Workers probe HDR metadata (bit depth, transfer/primaries, mastering data) and either tonemap to SDR (bt709/nv12) or fail fast with a categorized error before ffmpeg spins. Retryable errors (device busy, incomplete moov, missing ffmpeg) are tagged separately from fatal codec/pix_fmt issues so the dashboard can display actionable guidance.
- **Language preferences** - Audio/subtitle selection prefers Swedish, then English, then original, with dispositions preserved. Preferences are configurable per `operational.language_preferences` while keeping the Swedish-first default for legacy libraries.

## Configuration model

The SQLite config store (seeded from built-in defaults) captures:

- Libraries (`movies`, `series`, additional custom roots) with mount paths (full recursive scans enforced) and naming hints.
- Quality profile per library (resolution cap, bitrate budget, scaling rules, audio layout).
- Operational thresholds (max concurrent jobs, GPU temp cutoffs, disk usage guardrails).
- Notification sinks (Webhook, email) for warnings/errors.

All persisted entries are validated with Pydantic to reject invalid or Chromecast-incompatible combinations (e.g., HEVC, excessive bitrates) before they are stored.

## Production logging and monitoring

- Every container logs structured JSON with correlation IDs tied to file paths/checksums.
- Centralized log storage preserves `severity`, `source`, and `category` metadata for each entry, exposed via `/api/logs` with dashboard filters that default to INFO-and-above to keep noisy scans out of sight.
- Orchestrator exposes `/healthz`, `/readyz`, and `/metrics` endpoints for Docker health checks and Prometheus.
- Critical ffmpeg metrics (encoding speed, dropped frames) propagate to orchestrator to flag action-heavy titles requiring attention.
- Audit log tracks config changes and AI-agent commits for traceability.

## Deployment considerations

- Delivered as a Docker Compose stack optimized for Docker Desktop + WSL2. Bind mounts map Windows folders (`D:\Media\Movies`, etc.) into `/mnt/movies` in Linux containers.
- GPU access enabled by installing `nvidia-container-toolkit` within WSL2 and adding `runtime: nvidia` in compose.
- Compose profiles can disable watchers or encoding containers when running in documentation-only mode.
- Future Kubernetes deployment is feasible because components are stateless aside from orchestrator storage.
