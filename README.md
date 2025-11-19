# Chromecast Video Converter

Container-driven pipeline that keeps a media library Chromecast Gen 2/3 ready
through GPU-only transcoding. The orchestrator now runs as a Flask application
backed by a SQLite database and implements a database-driven finite state
machine for every media asset. A smart scanner keeps the database aligned with
the filesystem, while a persistent worker thread advances jobs through
`New -> Queued -> Processing -> Completed/Failed/Archived` states.

## Documentation map

- [`docs/01_architecture.md`](docs/01_architecture.md) - Component model,
  container build, and operational constraints.
- [`docs/user/01_getting_started.md`](docs/user/01_getting_started.md) - Stack
  prerequisites, configuration, and day-one operation for Docker Compose.
- [`docs/user/02_configuration.md`](docs/user/02_configuration.md) - Library
  configuration and mount alignment for the Flask orchestrator.
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
3. Copy `config/settings.yaml.template` to `config/settings.yaml` and adjust
   library profiles or operational limits; the defaults target Chromecast-safe
   H.264/AAC at 720p with guardrails for GPU temperature and disk usage.
   (The running stack always reads `config/settings.yaml`; keep the template
   as a reference copy only.)
4. Run `docker compose build` to create the orchestrator, watcher, and
   `gpu-ffmpeg` images locally.
5. Start the stack with `docker compose up`. The orchestrator mounts
   `./services/orchestrator/app`, so HTML updates are picked up on refresh
   without rebuilding.
6. Visit `http://localhost:9000/library` for the database-backed library view.
   Rescans and re-process actions are available directly from the UI and update
   the SQLite-backed finite state machine in real time.

### Orchestrator finite state machine

- **Database-backed MediaAssets** – SQLite with WAL enabled to allow concurrent
  reads and writes. Each asset tracks `file_path`, `file_hash`, `file_size`,
  modification time, state, retries, and any processing errors.
- **Smart scanner** – Walks the configured media root, syncing the database to
  disk. Hash matches trigger rename updates, modified files reset to `New`, and
  missing files are removed unless already `Archived`.
- **Robust worker** – A background loop claims `Queued` jobs, marks them
  `Processing`, runs ffmpeg, verifies outputs with ffprobe, and marks
  `Completed`/`Failed`/`Archived` with optional deletion of originals.
- **Flask dashboard** – `/library` shows paginated assets with colored badges
  (`Completed` green, `Archived` grey, `Failed` red) and lets operators rescan or
  re-queue assets when safe.

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
- The orchestrator service now ships with Flask 3.x and Flask-SQLAlchemy 3.x to
  support the database-backed finite state machine and server-side rendering.
- GPU workers continue to rely on ffmpeg with NVENC enabled; ensure NVIDIA
  drivers and CUDA runtimes are available on the host.
