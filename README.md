# Chromecast Video Converter

Repository for experimenting with a container-driven pipeline that keeps a media
library Chromecast Gen 2/3 friendly through GPU-only transcoding. The first goal
is to document a resilient architecture and the quality process AI coding
agents should follow when extending the system.

## Documentation map

- [`docs/01_architecture.md`](docs/01_architecture.md) - Component model, data
  flows, containers, and operational constraints.
- [`docs/02_ai_agent_process.md`](docs/02_ai_agent_process.md) - Expectations
  for AI coding agents, including quality gates and collaboration rules.
- [`docs/03_implementation_plan.md`](docs/03_implementation_plan.md) - Milestone
  plan that sequences implementation work and future user documentation.

Future commits will add the actual containers, orchestration logic, and
user-facing instructions described in these references.

## Getting started

1. Copy `config/quality.sample.yaml` to `config/quality.yaml` and adjust library
   profiles, Jellyfin settings, or operational thresholds as needed; keep the
   host-to-container paths defined in `docker-compose.yml` untouched unless you
   also update the volume mounts there.
2. Run `docker compose build` (locks in Python dependencies and GPU image).
3. Start the stack with `docker compose up`.
4. Visit `http://localhost:9000` to view the orchestrator dashboard, trigger a
   manual scan, or monitor job progress and metrics.

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
