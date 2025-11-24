# Agent Guide

This repository is agent-friendly and expects changes to preserve an operational MVP. Read this document before editing any files.

## Expectations

- Work in **short-lived feature branches** (e.g., `feature/<desc>`) and open PRs into `main`; never commit directly to `main` to respect branch protections.
- Keep the public-facing documentation in sync with the current behavior of the Docker Compose stack and orchestration services.
- Prefer incremental, narrowly scoped commits that pair code changes with matching documentation updates.
- Preserve GPU-only transcoding assumptions and avoid introducing CPU fallbacks.

## Quality gates

- Run `ruff check .` and `black --check .` before opening a PR; both are mandatory and enforced in review.
- Run relevant pytest targets when touching APIs/flow: `pytest services/orchestrator/tests/test_api_endpoints.py` at minimum for orchestrator changes. Add/extend tests alongside new endpoints, websocket events, pagination, or watcher behavior.
- Record any additional verification you run (manual scans, compose smoke tests) in the PR description.
- Do not mix unrelated refactors with feature or doc updates.

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
