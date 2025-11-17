# 01 - Architecture Overview

## Goals and constraints

- Monitor separate `movies` and `series` libraries exposed from Windows (bind mounts via Docker Desktop/WSL2).
- Keep every asset streamable on Chromecast Gen 2/3 without server-side transcoding.
- Guarantee GPU-only encoding on an NVIDIA RTX 3060 and cap resolution at 720p.
- Prioritize perceptual quality and smooth action playback while targeting 1.8-3.2 GB movies.
- Enforce H.264 (High, L4.1) video / AAC 192 kbps stereo audio (2 channels), yuv420p pixel format, nvenc preset `p5`, `-cq 18`, `-maxrate 8M`, `-bufsize 16M`, `-movflags +faststart`, and downscale-only filtering (`scale=-2:720:force_original_aspect_ratio=decrease`).
- Deliver production-grade logging, guardrails for invalid configs, and fault tolerance.

## Container topology

| Container | Base | Role |
| --- | --- | --- |
| `orchestrator` | Ubuntu LTS | Coordinates workers, applies policy, exposes API/logging, persists state in SQLite/Postgres. Primary configuration entrypoint and status dashboard. |
| `folder-watcher` | Alpine + `inotify-tools` | Watches bind-mounted `movies` and `series` folders, emits events to orchestrator via HTTP/WebSocket or message bus. Stateless and horizontally scalable. |
| `gpu-ffmpeg` | Ubuntu + FFmpeg + CUDA/NVIDIA runtime | Executes validation and transcode jobs using NVENC. Launches via orchestrator with bind-mounted file chunks and temp workspace. |
| `queue` (optional) | Redis | Buffers work to smooth spikes. |

All containers join a private Docker network. Bind mounts provide the Windows-host media folders and a `config/` directory containing YAML definitions for libraries and quality rules. NVIDIA Container Toolkit is required so `gpu-ffmpeg` can access the RTX 3060 from WSL2.

## Data flow

1. **Change detection** - Each `folder-watcher` instance monitors a root directory and reports file creates/modifies/deletes plus metadata (path, size, hash) to the orchestrator.
2. **Policy evaluation** - Orchestrator loads quality profiles (per movies/series) from `config/settings.yaml`. It validates config shape and warns about unsupported codecs/levels before persisting any change.
3. **Compliance check** - Orchestrator inspects new or updated files by invoking `gpu-ffmpeg` in probe mode to extract codecs, resolution, bitrate, and HDR flags. Files already compliant are flagged `ready`.
4. **Transcode scheduling** - Non-compliant files become jobs in a durable queue. Orchestrator throttles concurrent ffmpeg invocations to respect GPU memory and disk IO.
5. **Encoding** - `gpu-ffmpeg` receives a manifest (input path, target profile) and runs ffmpeg with pinned parameters: `-hwaccel cuda -hwaccel_output_format cuda -i <src> -vf "scale=-2:720:force_original_aspect_ratio=decrease" -c:v h264_nvenc -profile:v high -level 4.1 -preset p5 -cq 18 -maxrate 8M -bufsize 16M -pix_fmt yuv420p -movflags +faststart -c:a aac -b:a 192k -ac 2`. Audio/video map decisions come from the manifest.
6. **Verification** - Upon success, orchestrator triggers another probe to confirm specs, updates catalog metadata (JSON/SQLite), and rotates files (e.g., move original to `archive/` if configured).
7. **Observability** - Structured logs (JSON) flow to stdout for container log drivers and are centralized by the orchestrator in a SQLite-backed log store exposed via `/api/logs`. Metrics cover queue length, GPU utilization snapshots, and success ratios; alerts fire when policy violations or repeated job failures occur.

## User interface and manual controls

- A lightweight dashboard served from the orchestrator exposes health, queue metrics, and a manual scan button. The interface calls `/api/scan` to enqueue jobs on demand, so operators can trigger rescans before files are watched.
- The orchestrator also exposes `/api/events` for watchers or other adapters to notify about new media, plus `/api/jobs/{id}/status` so GPU workers can report progress.

## Jellyfin and optional integrations

- Optional Jellyfin integration can poll or receive webhooks from the local media server and call its `/Library/Refresh` API so our pipeline stays in sync with the catalog it already maintains.
- Jellyfin remains a trigger/metadata source; this stack keeps encoding independent so we can keep using GPU-only paths without inheriting Jellyfin’s transcoding engine.

## Fault tolerance and recovery

- **Idempotent jobs** - Each job references content by checksum, allowing safe retries.
- **Circuit breakers** - Orchestrator can pause scheduling if GPU temperature exceeds thresholds or storage free space is low.
- **Rollback strategy** - Originals persist until verification passes. Failures keep source files untouched and log detailed ffmpeg stderr for analysis.
- **Self-healing watchers** - `folder-watcher` restarts quickly (tiny Alpine image). If orchestrator is unavailable, watchers buffer events locally before replaying.

## Configuration model

`config/settings.yaml` (validated via JSON Schema) captures:

- Libraries (`movies`, `series`, additional custom roots) with mount paths, recursion depth, naming hints.
- Quality profile per library (resolution cap, bitrate budget, scaling rules, audio layout).
- Operational thresholds (max concurrent jobs, GPU temp cutoffs, disk usage guardrails).
- Notification sinks (Webhook, email) for warnings/errors.

Invalid combinations (e.g., requesting HEVC) are rejected with actionable errors so users cannot break Chromecast compatibility.

## Production logging and monitoring

- Every container logs structured JSON with correlation IDs tied to file paths/checksums.
- Orchestrator exposes `/healthz`, `/readyz`, and `/metrics` endpoints for Docker health checks and Prometheus.
- Critical ffmpeg metrics (encoding speed, dropped frames) propagate to orchestrator to flag action-heavy titles requiring attention.
- Audit log tracks config changes and AI-agent commits for traceability.

## Deployment considerations

- Delivered as a Docker Compose stack optimized for Docker Desktop + WSL2. Bind mounts map Windows folders (`D:\Media\Movies`, etc.) into `/mnt/movies` in Linux containers.
- GPU access enabled by installing `nvidia-container-toolkit` within WSL2 and adding `runtime: nvidia` in compose.
- Compose profiles can disable watchers or encoding containers when running in documentation-only mode.
- Future Kubernetes deployment is feasible because components are stateless aside from orchestrator storage.
