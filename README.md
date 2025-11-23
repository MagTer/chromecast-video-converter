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
2. Copy `.env.template` to `.env` and set `PATH_MOVIES`/`PATH_SERIES` to the
   host directories that hold your libraries. Relative values are resolved from
   the repository root (for example, `./media/movies`), while absolute paths
   work for mounted drives such as `/mnt/storage/Movies` or `D:\\Media\\Movies`
   on Windows.
3. To preconfigure profiles before first boot, copy
   `config/settings.yaml.template` to `config/settings.yaml` and adjust library
   profiles or operational limits. On startup, the orchestrator will import an
   existing `settings.yaml` (or fall back to the template) into a SQLite config
   store at `./logs/config.db`, validate it, and ignore the YAML files after the
   initial seed.
4. Run `docker compose build` to create the orchestrator, watcher, and
   `gpu-ffmpeg` images locally.
5. Start the stack with `docker compose up`. The orchestrator mounts
   `./services/orchestrator/app`, so HTML/API updates are picked up on refresh
   without rebuilding.
6. Visit `http://localhost:9000` for the dashboard and JSON API. Health checks
   live at `/api/healthz` and `/api/readyz`; logs from every container are
   centralized behind `/api/logs` with retention controls on the Configuration
   page (defaults to seven days).

   The orchestrator now persists GUI/API configuration updates to the
   SQLite-backed config store (`./logs/config.db`) so dashboard edits survive
   restarts without writing YAML on disk.

### MVP feature set

- **Orchestrator API & dashboard** – Serves health/ready endpoints, exposes
  queue metrics, persists centralized logs from every container with a
  retention slider and disk-usage stats, and lets operators trigger rescans of
  configured libraries. Log entries now include `severity`, `source`, and
  `category` fields, and the dashboard defaults to INFO-and-above filtering to
  keep verbose chatter out of the main view.
- **Job queue** – Redis-backed queue with pause/resume controls. GPU workers
  pull the next ready job from `/api/jobs/next`, update status back to the API,
  and honor the current profile configuration.
- **Folder watcher** – Alpine container monitoring bind-mounted `movies` and
  `series` roots. Streams create/modify/delete events (with file metadata) to
  the orchestrator with optional buffering and retry backoff so newly added or
  replaced files are queued immediately and removals are reflected in the
  library catalog.
- **Encoding profiles** – Centralized in a SQLite config store seeded from
  `config/settings.yaml.template` (or an existing `settings.yaml`) and editable
  via `/api/config/encoding`. Profiles target Chromecast Gen 2/3 constraints
  (H.264 High, level 4.1, 720p, capped bitrate) with AAC stereo audio and
  dropdowns for NVENC presets, CQ targets, and a 30 fps ceiling that keeps
  every audio track mapped as stereo AAC.
- **Verification hooks** – After startup, the orchestrator scans configured
  libraries and preloads jobs for anything not already compliant. On success,
  progress is reflected in the dashboard and metrics endpoint.
- **Library catalog** – The orchestrator now persists library entries with
  statuses for pending, converting, converted, failed, and removed items.
  Operators can list entries, trigger reprocessing, or request removal of
  originals (after confirming converted outputs exist) via the API.

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
