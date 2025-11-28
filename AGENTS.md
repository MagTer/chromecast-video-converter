# Agent Guide

This repository is agent-friendly and expects changes to preserve an operational MVP. Read this document before editing any files.

## Expectations

- Work in **short-lived feature branches** (e.g., `feature/<desc>`) and open PRs into `main`; never commit directly to `main` to respect branch protections.
- Keep the public-facing documentation in sync with the current behavior of the Docker Compose stack and orchestration services.
- Prefer incremental, narrowly scoped commits that pair code changes with matching documentation updates.
- Preserve GPU-only transcoding assumptions and avoid introducing CPU fallbacks.

## Quality gates & Verification Strategy

**The CI pipeline is strict. To avoid failure, you must follow this verification sequence EXACTLY before every submission:**

1.  **Linting (Root Level)**:
    *   Run `ruff check . --fix` from the repository root. **Do not run on subsets of files**, as this misses cross-file issues or files you forgot you touched.
    *   Run `black .` from the repository root.
    *   *If you make any code changes after this step (even one line), you must start over.*

2.  **Testing**:
    *   Run tests for each service **separately** to avoid namespace collisions (both services use `app` package):
        *   `pytest services/orchestrator/tests/`
        *   `pytest services/gpu-ffmpeg/tests/`
        *   `pytest services/gpu-ffmpeg/test_worker.py`
    *   Ensure all tests pass locally.

3.  **Final Check**:
    *   Run `ruff check .` and `black --check .` one last time to ensure no regressions were introduced during fixes.

**Do not submit if any of these steps fail.**

## Keep these behaviors in sync

- **Runtime library management**: `/api/libraries` POST/DELETE must align with dashboard add/remove UX and docs.
- **WebSocket updates**: message shapes `entry-update`, `job-update`, `library-update` must stay consistent with frontend handlers.
- **Pagination contract**: `/api/library/entries` `limit/offset/include_total` and UI load-more behavior must match.
- **Watcher persistence**: event spool defaults (`EVENT_SPOOL_FILE`, `EVENT_SPOOL_MAX_BYTES`) and replay-on-start expectations.
- **GPU-only encoding**: no CPU fallbacks; profile validation must preserve Chromecast constraints.

## Documentation touchpoints

- Update `README.md` quick tasks when changing user-facing flows.
- User guides: `docs/user/01_getting_started.md`, `docs/user/02_configuration.md`, `docs/user/03_api_reference.md` (API details).
- Architecture: `docs/01_architecture.md` for flow/diagram updates (libraries CRUD, websocket, spool).
- Process: `docs/02_ai_agent_process.md` for workflow and quality checklist.

## References

- The detailed collaboration process lives in `docs/02_ai_agent_process.md`. Use it for planning, testing, and review-ready summaries.
