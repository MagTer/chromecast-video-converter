# Agent Architecture & Workflow Guide

## 1. Project Context & Stack
**Chromecast Video Converter** is a GPU-only transcoding stack designed to keep bound media libraries compatible with Chromecast Gen 2/3 devices. It operates via an HTTP-orchestrated queue, GPU-accelerated ffmpeg workers, and a watchdog-powered file monitor.

- **Services:** Docker Compose stack with four services:
  - `orchestrator`: FastAPI service (`services/orchestrator`) handling coordination, API, and dashboard.
  - `folder-watcher`: Watchdog service (`services/folder-watcher`) monitoring filesystem events.
  - `gpu-ffmpeg`: Worker (`services/gpu-ffmpeg`) performing NVENC-accelerated transcoding.
  - `redis`: Message broker and state store.
- **Data Flow:** Media libraries mount twice (`/watch/...` and `/media/...`). The orchestrator normalizes prefixes and persists catalog/config/log data in SQLite under `./data`.
- **Core Constraints:** 
  - GPU pipelines must remain **NVENC-first**. CPU stages are only allowed as explicit fallbacks in retry pipelines. Do not introduce CPU-only modes as a new default.
  - WebSocket payloads (`job-update`, `entry-update`, `library-update`) and API contracts (pagination) must remain stable for the dashboard.

## 2. Critical Workflow Rules

### A. Git & Branching Strategy
- **Feature Branches:** Create short-lived branches (`feature/<desc>`, `bugfix/<issue>`) off `main`. **Never push directly to `main`.**
- **Atomic Commits:** Keep commits focused (code + matching docs/tests). Split large efforts into reviewable slices.
- **PR Descriptions:** Reference relevant documentation sections (`docs/README.md`, `docs/user/...`) when behavior changes.

### B. Anti-Spaghetti Architecture
- **Service Isolation:** Logic must remain strictly within its service boundary (`services/orchestrator`, `services/gpu-ffmpeg`). Shared logic should only exist if explicitly designed as a common library (currently none; duplicate small helpers if needed rather than coupling services tightly).
- **File Granularity:**
  - **Routers:** Keep `services/orchestrator/app/routers` focused on HTTP interface definition. Move complex business logic to `services/orchestrator/app/services`.
  - **Workers:** `services/gpu-ffmpeg/app/worker.py` should focus on job lifecycle. FFMpeg command construction belongs in `ffmpeg_builder.py`.
- **Architecture Invariants:**
  - `/api/libraries` add/remove operations must match dashboard UX.
  - Log ingestion must preserve `severity`, `source`, `category`, and `request_id` fields.
  - GPU worker telemetry (`/api/workers/telemetry`) must maintain its payload shape to feed the queue header.

### C. Implementation Strategy
1.  **Plan:** Analyze the request. If it involves complex refactoring or system-wide analysis, use `codebase_investigator` first.
2.  **Dependencies:** Verify libraries/frameworks in `requirements.txt` or `pyproject.toml` before importing.
3.  **Code:** Implement changes adhering to the existing style (Black/Ruff).
4.  **Verify:** Run the unified quality check script.

## 3. Code Quality & Strictness
- **Formatting:** Python is formatted with **Black** and linted via **Ruff**.
- **Type Hints:** Prefer type hints for all function signatures.
- **Comments:** Keep changes small and well-commented only when logic is non-obvious. Avoid large prose blocks in code.
- **File Organization:** Mirror existing file organization. Add or adjust tests alongside code changes, especially for config validation, watcher behavior, and ffmpeg command generation.

## 4. Verification & Testing
**Running Tests & Quality Checks**
DO NOT run `pytest` or `ruff` directly. Always use the unified entry point which handles multiple services and environment variables correctly:

```bash
python scripts/code_check.py
```

This script performs:
1.  Ruff Linting (fixing fixable errors)
2.  Black Formatting
3.  Orchestrator Tests (with correct `PYTHONPATH`)
4.  GPU Worker Tests (with correct `PYTHONPATH`)

**Do not submit a PR if this script fails.**

## 5. Documentation
- **Root README:** End-user focused.
- **Architecture:** `docs/architecture/README.md` covers data flow and container roles.
- **User Docs:** Update these whenever behavior changes:
  - `docs/user/01_getting_started.md` (Setup/Run)
  - `docs/user/02_configuration.md` (Env vars, tuning)
  - `docs/user/03_api_reference.md` (Endpoint list)
- **Process:** For process updates, amend `docs/02_ai_agent_process.md` plus this file.

**Boundaries & Safety:**
- **Never** commit secrets/credentials.
- **Ask first** when introducing new external services or changing the job queue/storage model.
- **Never** re-enable CPU-only encoding paths as a silent fallback without explicit configuration.