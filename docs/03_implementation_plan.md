# 03 - Implementation & Documentation Plan

This plan sequences the work needed to turn the documented architecture into a running, production-capable stack and defines how user-facing documentation will be produced.

## Milestones

1. **Foundational repo scaffolding**
   - Initialize Git project structure, base README, and architecture/process docs (this commit).
   - Set up baseline tooling configs (editorconfig, lint settings, pre-commit hooks).
2. **Container scaffolding**
   - Create `docker-compose.yml` with `orchestrator`, `folder-watcher`, `gpu-ffmpeg`, and `redis` services.
   - Stub Dockerfiles: Ubuntu base with Python runtime, Alpine watcher with `inotify-tools`, Ubuntu ffmpeg image with CUDA support.
   - Wire NVIDIA runtime configuration for RTX 3060.
3. **Orchestrator MVP**
   - Implement Python service (FastAPI + SQLModel/SQLite) for configs, job queue management, and API endpoints.
   - Build config loader/validator against JSON Schema.
   - Expose health and metrics endpoints.
4. **Watchers and job ingestion**
   - Package Alpine watcher that emits filesystem events via HTTP/WebSocket.
   - Add deduplication, rescan scheduling, and integrity checks.
   - Expand orchestrator to persist file metadata and compliance status.
5. **GPU encoding pipeline**
   - Wrap ffmpeg invocations with manifest-driven scripts enforcing required parameters.
   - Introduce verification probes, retry policies, and archive handling.
   - Add automated tests for command generation, job lifecycle, and error handling.
6. **Observability and resilience hardening**
   - Integrate structured logging, metrics exporters, alert rules, and incident logging.
   - Stress-test queue under load, validate GPU throttling and pause/resume flows.
7. **User-facing documentation & release prep**
   - Draft installation, configuration, and troubleshooting guides (see below).
   - Provide sample configs, compose profiles, and smoke-test scripts.
   - Cut the first tagged release and publish container images.

## User documentation roadmap

- **docs/user/01_getting_started.md** - Prerequisites (Docker Desktop, WSL2, NVIDIA toolkit), repo clone, compose launch.
- **docs/user/02_configuration.md** - Explain `config/quality.yaml`, library definitions, and guardrails.
- **docs/user/03_operations.md** - Routine tasks (monitoring logs, checking queue status, pausing jobs, verifying GPU load).
- **docs/user/04_troubleshooting.md** - Common issues (permission errors, GPU not detected, ffmpeg failures) with diagnostic steps.
- **docs/user/05_faq.md** - Clarify quality targets, storage considerations, and future codec plans.

These documents will be produced during Milestone 7 once the system is feature-complete enough to offer accurate, actionable guidance.
