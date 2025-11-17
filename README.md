# Chromecast Video Converter

Container-driven pipeline that keeps a media library Chromecast Gen 2/3 ready
through GPU-only transcoding. The MVP is operational: the orchestrator exposes a
dashboard and JSON API, a Redis-backed job queue coordinates GPU workers, and an
Alpine watcher feeds file-system events into the system.

## Documentation map

- [`docs/01_architecture.md`](docs/01_architecture.md) - Component model, data
  flows, containers, and operational constraints now implemented in the MVP.
- [`docs/user/01_getting_started.md`](docs/user/01_getting_started.md) - Stack
  prerequisites, configuration, and day-one operation.
- [`docs/user/02_configuration.md`](docs/user/02_configuration.md) - Details on
  aligning Compose mounts and orchestrator library definitions.
- [`docs/02_ai_agent_process.md`](docs/02_ai_agent_process.md) - Expectations
  for AI coding agents. See `AGENTS.md` for the quick-start rules and quality
  gates.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) - Current roadmap, gap analysis, and
  staged steps toward a production-ready release.

## Getting started

1. Ensure Docker (with NVIDIA Container Toolkit for GPU hosts) is available.
2. Copy `config/quality.sample.yaml` to `config/quality.yaml` and adjust library
   profiles or operational limits; the defaults target Chromecast-safe H.264/AAC
   at 720p with guardrails for GPU temperature and disk usage.
3. Run `docker compose build` to create the orchestrator, watcher, and
   `gpu-ffmpeg` images locally.
4. Start the stack with `docker compose up`. The orchestrator mounts
   `./services/orchestrator/app`, so HTML/API updates are picked up on refresh
   without rebuilding.
5. Visit `http://localhost:9000` for the dashboard and JSON API. Health checks
   live at `/api/healthz` and `/api/readyz`; logs are available at `/api/logs`.

### MVP feature set

- **Orchestrator API & dashboard** – Serves health/ready endpoints, exposes
  queue metrics, streams recent logs from the in-memory log handler, and lets
  operators trigger rescans of configured libraries.
- **Job queue** – Redis-backed queue with pause/resume controls. GPU workers
  pull the next ready job from `/api/jobs/next`, update status back to the API,
  and honor the current profile configuration.
- **Folder watcher** – Alpine container monitoring bind-mounted `movies` and
  `series` roots. Emits create/modify events to the orchestrator so newly added
  files are queued immediately.
- **Encoding profiles** – Centralized in `config/quality.yaml` and editable via
  `/api/config/encoding`. Profiles target Chromecast Gen 2/3 constraints (H.264
  High, level 4.1, 720p, capped bitrate) with AAC stereo audio.
- **Verification hooks** – After startup, the orchestrator scans configured
  libraries and preloads jobs for anything not already compliant. On success,
  progress is reflected in the dashboard and metrics endpoint.

### GPU access inside Docker Compose

- Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/index.html)
  on the Docker host so the `gpu-ffmpeg` service can reach NVENC devices.
- The `gpu-ffmpeg` service now adds the `NVIDIA_VISIBLE_DEVICES=all` and
  `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility` environment variables to
  make the GPU encoder visible inside the container.
- Compose also applies cgroup rules (`c 195:* rmw`, `c 508:* rmw`) so the
  container can open `/dev/nvidia*` without hitting permission errors when the
  stack is launched from WSL2 or other constrained environments.

### Dependency refresh

- The folder watcher image now tracks Alpine 3.20 so its inotify tooling stays on
  a supported security baseline.
- The orchestrator service pins FastAPI 0.115, Pydantic 2.9, and Uvicorn 0.30
  along with refreshed Jinja2 and HTTPX releases to pick up the newest ASGI
  features and fixes.
- GPU workers also track HTTPX 0.27.2 so request handling matches the
  orchestrator's HTTP stack.
