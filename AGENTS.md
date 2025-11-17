# Agent Guide

This repository is agent-friendly and expects changes to preserve an operational MVP. Read this document before editing any files.

## Expectations

- Keep the public-facing documentation in sync with the current behavior of the Docker Compose stack and orchestration services.
- Prefer incremental, narrowly scoped commits that pair code changes with matching documentation updates.
- Preserve GPU-only transcoding assumptions and avoid introducing CPU fallbacks.

## Quality gates

- Run `ruff check .` and `black --check .` before opening a PR; both are mandatory and enforced in review.
- Record any additional verification you run (manual scans, compose smoke tests) in the PR description.
- Do not mix unrelated refactors with feature or doc updates.

## References

- The detailed collaboration process lives in `docs/02_ai_agent_process.md`. Use it for planning, testing, and review-ready summaries.
