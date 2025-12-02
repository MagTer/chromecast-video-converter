# Documentation Guide

This directory holds everything needed to run, operate, and extend the Chromecast Video Converter stack. Start with the user guides to bring the Docker Compose deployment online, then dive into the architecture notes for design-level context.

## Quick map

- `user/01_getting_started.md` — prerequisites, `.env` layout, and day-one workflow for running the orchestrator, folder watcher, GPU worker, and Redis services.
- `user/02_configuration.md` — how runtime configuration is stored, how the dashboard and APIs edit libraries/profiles, and which environment variables tune watcher buffering, GPU worker limits, and log retention.
- `user/03_api_reference.md` — concise HTTP/WebSocket reference that mirrors the FastAPI routers in `services/orchestrator/app/routers`.
- `architecture/README.md` — container topology, storage layout, event flow, and the GPU worker pipeline (ffprobe, NVENC, job retries, log fan-out).
- `02_ai_agent_process.md` — collaboration workflow (planning, quality gates, review-ready handoffs). `AGENTS.md` summarizes the same rules in machine-readable form.
- `ROADMAP.md` — future work items and dependency tracking.

See the repository root `README.md` for high-level positioning; all implementation details live here.
