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
