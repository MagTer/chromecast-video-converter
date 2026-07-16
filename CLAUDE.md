# Agent Guide

## Project Overview

- Docker Compose stack with four services: FastAPI orchestrator (`services/orchestrator`), folder watcher (`services/folder-watcher`), GPU ffmpeg worker (`services/gpu-ffmpeg`), and Redis.
- Media libraries mount twice (`/watch/...` and `/media/...`); the orchestrator normalizes both prefixes and persists catalog/config/log data in SQLite under `./data`.
- GPU pipelines must remain NVENC-first; CPU stages only appear when retry pipelines deliberately fall back. Do not introduce CPU-only modes as a new default.
- Besides `convert` jobs there are lightweight `verify` jobs (ffprobe-only Chromecast compliance check, no GPU) and `delete` jobs. Verify jobs never alter entry status and never enter the encode retry ladder.
- Prebuilt images are published to GHCR by `.github/workflows/docker-publish.yml` on every push to `main` (per-service change detection; the heavy `ffmpeg-cuda` base only rebuilds when `Dockerfile.ffmpeg`/patches change). Check `org.opencontainers.image.revision` on an image to see which commit it was built from.

## Git Workflow

- Create short-lived feature branches (`feature/<desc>`, `bugfix/<issue>`, etc.) off `main`. Never push directly to `main`.
- Keep commits focused (code + matching docs/tests). Split large efforts into reviewable slices.
- Reference the relevant doc section (`docs/README.md`, `docs/user/...`, `docs/architecture/README.md`) in PR descriptions when behavior changes.

## Development & Testing Workflow

This project uses a unified root-level `pyproject.toml` for dependency management and testing configuration.

### 1. Environment Setup

```bash
python3 -m venv .venv          # create venv if not exists
source .venv/bin/activate
pip install -e .[dev]          # all dependencies including dev tools
```

### 2. Verify Code

`scripts/code_check.py` runs the same checks as CI: `ruff check .`, `black --check .`, `mypy` per service, and `pytest` per service.

```bash
./scripts/code_check.py
```

Fix automatically with `ruff check . --fix` and `black .`. Do not submit a PR if `scripts/code_check.py` fails.

### 3. End-to-end verification (optional but encouraged for API/GUI work)

The orchestrator runs fine outside Docker against a throwaway Redis:

```bash
docker run -d --rm --name dev-redis -p 127.0.0.1:6399:6379 redis:8-alpine
cd services/orchestrator
JOB_QUEUE=redis://127.0.0.1:6399/0 CONFIG_DB_PATH=/tmp/dev/config.db \
LOG_DB_PATH=/tmp/dev/logs.db LIBRARY_DB_PATH=/tmp/dev/library.db \
python -m uvicorn app.main:app --port 8099
```

You can simulate a worker over the API alone: claim with `GET /api/jobs/next?worker_id=x`, post `POST /api/jobs/{id}/status` (optionally with a `compliance` verdict), then `POST /api/jobs/{id}/ack`.

## Code Style & Quality

- Python is formatted with Black and linted via Ruff; prefer type hints and avoid unused imports/variables.
- Keep changes small and well-commented only when logic is non-obvious (short inline comments > large prose blocks).
- Mirror existing file organization (FastAPI routers under `services/orchestrator/app/routers`, shared helpers under `services/orchestrator/app/services`, etc.).
- Add or adjust tests alongside code changes, especially for config validation, watcher behavior, ffmpeg command generation, and API payloads.

## Frontend (dashboard) Notes

- The GUI is a single vanilla-JS module (`services/orchestrator/app/static/js/app.js`) plus `templates/index.html` and `static/css/app.css`. No build step.
- **Bump the cache-buster** (`app.js?v=N` in `index.html`) whenever `app.js` changes.
- Escape all interpolated data in `innerHTML` templates via `escapeHtml()`.
- Never display fabricated data: `formatPipeline()` returns `—` when no pipeline exists — do not reintroduce default "GPU/GPU/GPU" fallbacks.
- Summary cards read whole-table totals from `GET /api/library/entries/summary`; do not compute totals from the client-side pagination window.
- `libraryStatus` (`#library-status`) carries action feedback; WebSocket connection state lives in `#ws-status` in the nav. Keep them separate.
- Polled fetches must swallow transient network errors (see `refreshQueueState`) so a server restart doesn't spray unhandled rejections.

## Architecture Invariants

- `/api/libraries` runtime add/remove must match dashboard UX and docs.
- `/api/library/entries` takes `limit`, `offset`, `status`, `library`, `compliance` (`compliant`/`noncompliant`/`unverified`), `query` (substring match on path/library), and `include_total`. Default response is an array sorted by `updated_at` desc; `include_total=true` wraps it in `{items, total, limit, offset}`. Any change to this contract requires coordinated frontend/docs updates.
- Compliance verdict lifecycle: verdicts (`output_compliant` + `compliance_detail`) are attached by convert jobs post-encode and by verify jobs; they are **cleared whenever an entry is (re)queued to pending** (both `update_status` and `upsert` enforce this). Scans auto-queue verify jobs for converted entries lacking a verdict, deduplicated per path in Redis.
- `job_history` rows carry `job_type` (`convert`/`verify`/`delete`); `record_job_history` must keep passing it so the History page can distinguish job kinds.
- WebSocket payloads (`job-update`, `entry-update`, `library-update`), watcher spool semantics, and the pagination contract above are contractually shared with the dashboard.
- Watcher defaults (`WATCH_POLLING`, `EVENT_BUFFER_SECONDS`, `EVENT_SPOOL_FILE`, `EVENT_SPOOL_MAX_BYTES`) and spool replay-on-start behavior are canonical; keep code/docs in sync.
- Log ingestion keeps `severity`, `source`, `category`, and `request_id` fields. Preserve these when touching logging or telemetry.
- GPU worker telemetry (`/api/workers/telemetry`) feeds the queue header and the GPU pill tooltip; keep payload shape stable.
- SQLite schema changes need lightweight ALTER TABLE migrations in the store's `_ensure_schema()` (see `LibraryEntryStore` and `JobHistoryStore`) — `create_all` only creates missing tables.

## Documentation Expectations

- Root `README.md` is end-user focused; direct readers to `docs/README.md` and `docs/architecture/README.md`.
- Update user docs whenever behavior changes:
  - `docs/user/01_getting_started.md` (setup/run instructions)
  - `docs/user/02_configuration.md` (env vars, watcher/worker tuning, queue controls)
  - `docs/user/03_api_reference.md` (endpoint list tied to FastAPI routers)
  - `docs/architecture/README.md` (data flow, container roles)
- For process updates, amend `docs/02_ai_agent_process.md` plus this file.

## Boundaries & Safety

- **Always:** run `scripts/code_check.py` before sharing work, keep configuration/documentation synchronized with code, and document any manual steps taken during testing.
- **Ask first:** when introducing new external services, changing the job queue/storage model, or altering ffmpeg/gpu runtime dependencies.
- **Never:** commit secrets/credentials, edit production deployment settings outside `config/` unless requested, or re-enable CPU-only encoding paths as a silent fallback.

Refer to `docs/02_ai_agent_process.md` for the broader collaboration workflow.
