# 01 - Getting Started

This guide walks through prerequisites, configuration, and day-one operation of the Chromecast Video Converter MVP. The stack is intended for local GPU-equipped hosts and runs entirely via Docker Compose.

## Prerequisites

- Docker Desktop or a compatible Docker Engine installation.
- NVIDIA GPU with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/index.html) installed so the `gpu-ffmpeg` service can reach NVENC devices.
- Open network access to `localhost:9000` for the dashboard/API and `localhost:6379` if you want to inspect Redis directly.

## Initial setup

1. Copy `config/settings.yaml.template` to `config/settings.yaml`. The stack
   always loads `config/settings.yaml`; keep the template as a copy seed only.
2. Adjust library roots or profiles if your media lives outside the default `./media/movies` and `./media/series` mounts. Keep the left-hand side of the Compose volume mounts aligned with your host paths and use the corresponding `/watch/...` paths inside the YAML.
3. Review operational guardrails in the config (GPU temperature cutoff, disk usage limits, and whether originals are deleted after successful verification).
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
- Queue controls: `/api/queue/pause` and `/api/queue/resume` allow operators to throttle work when storage or thermal limits are reached.
- Logging: `/api/logs` returns recent log entries across the orchestrator, GPU workers, and folder watcher. Configure the retention window (default 7 days) and review log disk usage from the Configuration page.
- Job lifecycle:
  - `/api/scan` triggers a (re)scan of configured libraries to enqueue work.
  - `/api/jobs/next` supplies the next job to GPU workers.
  - `/api/jobs/{id}/status` records progress and completion updates from workers.

## Media watcher behavior

The `folder-watcher` container monitors the mounted `movies` and `series` directories. Create or modify files under those paths and the watcher will notify the orchestrator, which then queues verification/transcoding jobs based on the active profile.

## Cleanup and troubleshooting

- Stop the stack with `Ctrl+C` in the Compose terminal or `docker compose down` from another shell.
- If you adjust volume mounts or the quality config, restart the stack so new paths and guardrails take effect.
- Redis data persists in the `redis_data` volume; remove it with `docker volume rm chromecast-video-converter_redis_data` if you want a clean queue state.
