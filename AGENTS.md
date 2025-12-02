# Agent Guide

## Project Overview

- Docker Compose stack with four services: FastAPI orchestrator (`services/orchestrator`), folder watcher (`services/folder-watcher`), GPU ffmpeg worker (`services/gpu-ffmpeg`), and Redis.
- Media libraries mount twice (`/watch/...` and `/media/...`); orchestrator normalizes both prefixes and persists catalog/config/log data in SQLite under `./data`.
- GPU pipelines must remain NVENC-first; CPU stages only appear when retry pipelines deliberately fall back. Do not introduce CPU-only modes as a new default.
- WebSocket payloads (`job-update`, `entry-update`, `library-update`), watcher spool semantics, and `/api/library/entries` pagination (array response with `limit`/`offset` support) are contractually shared with the dashboard.

## Git Workflow

- Create short-lived feature branches (`feature/<desc>`, `bugfix/<issue>`, etc.) off `main`. Never push directly to `main`.
- Keep commits focused (code + matching docs/tests). Split large efforts into reviewable slices.
- Reference the relevant doc section (`docs/README.md`, `docs/user/...`, `docs/architecture/README.md`) in PR descriptions when behavior changes.

## Build & Test

Run the following from the repo root in the order listed. If you modify any tracked file after a step, restart the sequence from step 1.

1. `ruff check . --fix`
2. `black .`
3. `pytest services/orchestrator/tests/`
4. `pytest services/gpu-ffmpeg/tests/`
5. `pytest services/gpu-ffmpeg/test_worker.py`
6. Final verification: `ruff check .` and `black --check .`

Do not submit a PR if any command fails.

## Code Style & Quality

- Python is formatted with Black and linted via Ruff; prefer type hints and avoid unused imports/variables.
- Keep changes small and well-commented only when logic is non-obvious (short inline comments > large prose blocks).
- Mirror existing file organization (FastAPI routers under `services/orchestrator/app/routers`, shared helpers under `services/orchestrator/app/services`, etc.).
- Add or adjust tests alongside code changes, especially for config validation, watcher behavior, ffmpeg command generation, and API payloads.

## Documentation Expectations

- Root `README.md` is end-user focused; direct readers to `docs/README.md` and `docs/architecture/README.md`.
- Update user docs whenever behavior changes:
  - `docs/user/01_getting_started.md` (setup/run instructions)
  - `docs/user/02_configuration.md` (env vars, watcher/worker tuning, queue controls)
  - `docs/user/03_api_reference.md` (endpoint list tied to FastAPI routers)
  - `docs/architecture/README.md` (data flow, container roles)
- For process updates, amend `docs/02_ai_agent_process.md` plus this file.

## Architecture Invariants

- `/api/libraries` runtime add/remove must match dashboard UX and docs.
- `/api/library/entries` takes `limit` + `offset` and returns an array sorted by `updated_at`. Any change to this contract requires coordinated frontend/docs updates.
- Watcher defaults (`WATCH_POLLING`, `EVENT_BUFFER_SECONDS`, `EVENT_SPOOL_FILE`, `EVENT_SPOOL_MAX_BYTES`) and spool replay-on-start behavior are canonical; keep code/docs in sync.
- Log ingestion keeps `severity`, `source`, `category`, and `request_id` fields. Preserve these when touching logging or telemetry.
- GPU worker telemetry (`/api/workers/telemetry`) feeds the queue header; keep payload shape stable.

## Boundaries & Safety

- **Always:** run the full lint/test suite before sharing work, keep configuration/documentation synchronized with code, and document any manual steps taken during testing.
- **Ask first:** when introducing new external services, changing the job queue/storage model, or altering ffmpeg/gpu runtime dependencies.
- **Never:** commit secrets/credentials, edit production deployment settings outside `config/` unless requested, or re-enable CPU-only encoding paths as a silent fallback.

Refer to `docs/02_ai_agent_process.md` for the broader collaboration workflow.
