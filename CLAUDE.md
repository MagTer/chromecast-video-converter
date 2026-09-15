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
- Log ingestion keeps `severity`, `source`, `category`, and `request_id` fields end-to-end (worker/watcher handler → `/api/logs/ingest` → `LogIngestEvent` schema → SQLite → `/api/logs` filters). Preserve these when touching logging or telemetry.
- Logging conventions: worker/watcher handlers attach to the **service-root logger** (`gpu-ffmpeg`, `folder-watcher`) so new module loggers must live under that namespace or their logs never ship to the orchestrator; orchestrator loggers are named `orchestrator.<area>` (not module `__name__`, which would split the source into "app"); store-bound handlers use a plain `%(message)s` formatter — metadata travels as columns, not baked into the text; log messages reference jobs by the short 8-char id (`job_id[:8]`), which the GUI's "View logs" buttons rely on.
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

- **The API is unauthenticated by design** (trusted home LAN; see "Security & network exposure" in `docs/user/02_configuration.md`). Never add endpoints that make destructive actions easier to trigger remotely without flagging the security impact in the PR.
- **The delete gate is an invariant:** every code path that unlinks an original media file must go through the worker-side gate in `handle_delete_job`/`_maybe_remove_original` — output exists, duration matches the source within 1s, an ffprobe compliance check passes, and the source's subtitles are preserved (embedded in the output or as `.srt` sidecars; see `_subtitle_report`). Failed/refused delete jobs must not enter the encode retry ladder and must leave the entry as `converted`. Do not add new unlink paths that bypass this.
- **Always:** run `scripts/code_check.py` before sharing work, keep configuration/documentation synchronized with code, and document any manual steps taken during testing.
- **Ask first:** when introducing new external services, changing the job queue/storage model, or altering ffmpeg/gpu runtime dependencies.
- **Never:** commit secrets/credentials, edit production deployment settings outside `config/` unless requested, or re-enable CPU-only encoding paths as a silent fallback.

Refer to `docs/02_ai_agent_process.md` for the broader collaboration workflow.

## Operational Notes (September 2026)

- **Chromecast profile is now Constrained Baseline @ L3.1** (changed 2026-09-13 in
  runtime `data/config.db` → `quality.profiles.chromecast`): `bframes 0`,
  `bufsize 10M`, `adaptive_b_frames false`, max 1280 px. Rationale: legacy
  Chromecast receivers reject H264 High profile and force real per-playback
  transcoding on Jellyfin; baseline outputs are accepted and only remuxed
  (stream copy). Runtime DB is not in git — the decision lives here.
- **Mass re-encode procedure (2026-09-13, 789 files)**: renamed every
  `X-chromecast.mp4` → `X.mp4`; watcher/scan then re-queued conversions under
  the new profile. No originals were left (gen-2 from already-compressed
  files). Windows lesson: paths >260 chars fail in PowerShell 5.1
  `Rename-Item`/`Test-Path` even though the file exists — use the `\\?\`
  prefix with `[System.IO.File]::Move`.
- **Spurious verify jobs (root-caused & fixed 2026-09-15)**: `is_converted`
  treats the output as finished as soon as `X-chromecast.mp4` exists with a
  newer mtime, but ffmpeg creates that file while encoding. The 5-minute scan
  therefore saw each in-flight encode as `CONVERTED` with `output_compliant is
  None` and queued a verify (roughly one per conversion). The verdict itself
  *is* persisted on completion (`sync_entry_from_job`) — the verify was
  redundant and accumulated because the single worker only served converts.
  Fixes: (1) the worker encodes to `X-chromecast.part.mp4` and atomically
  `os.replace`s it after `_validate_output` (the scanner ignores the `.part`
  name), and (2) `record_library_entry` keeps `converting` and skips the verify
  while `JobManager.active_job_for_path` reports an in-flight convert job.
  Legacy queued verify jobs are harmless — do not bulk-XDEL in-flight work.
- **HDR tonemap fixes (2026-09-15)**: (1) `_build_filter_chain` now scales to
  the target size *before* the `zscale`/`tonemap` chain, so odd source
  dimensions (e.g. a 1329×720 HDR file) no longer abort zscale with "image
  dimensions must be divisible by subsampling factor" — and tonemapping runs at
  720p instead of 4K. (2) Tonemapped outputs drop the source's
  mastering-display / content-light / HDR10+ frame side data with
  `sidedata=delete:type=...` in the filter chain, so libx264/NVENC cannot
  re-emit it and the MP4 muxer writes no `mdcv`/`clli` boxes — otherwise
  `is_hdr()` flagged a correctly BT.709 output as noncompliant (a plain
  `-bsf:v filter_units=remove_types=6` is NOT enough: it strips bitstream SEI
  but the muxer still writes the boxes from frame side data). (3) `is_hdr()`
  now trusts an explicit SDR transfer (bt709 etc.) over residual side data;
  untagged transfers still fall back to side data. Existing affected outputs
  (HDR/DV movies) must be reprocessed (force) to gain the clean SDR metadata.
- **ffprobe over the Docker Desktop 9p/drvfs mount is slow** (minutes for
  multi-GB files under load). `GPU_FFPROBE_TIMEOUT` defaults to 120 s and a
  timeout yields a one-shot error verdict (never retried, since
  `output_compliant` is then non-NULL). Consider raising it; the delete gate
  re-probes fresh at deletion time, so a wrong verify verdict is cosmetic.
- **9p mount flakiness**: scans can transiently report "0 media files" and
  mark entries `removed` while the host filesystem is fine; it self-heals and
  the scheduled scan (backstop for lost file events) re-tracks everything.
  Suspect the mount whenever the queue suddenly looks empty.
- **Queue hygiene**: the Redis stream retains orphaned entries from earlier
  eras (worker reads newest first, so they never run). Harmless but visible
  in the dashboard job list; can be trimmed with a one-off XDEL/XTRIM pass.
