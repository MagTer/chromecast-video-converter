# 02 - AI Coding Agent Process

This guide standardizes how AI coding agents contribute to the Chromecast Video Converter project with strong emphasis on quality, maintainability, and reproducibility.

## Operating principles

- **Documentation first** - Confirm requirements and update relevant docs whenever behavior changes or new assumptions appear.
- **Plan before execution** - Break down complex work into verifiable steps, track progress, and adapt when new information surfaces.
- **Quality gates over speed** - Favor correctness, deterministic builds, and extensive logging/telemetry.
- **GPU-conscious development** - Preserve GPU-only transcoding, avoid CPU fallbacks, and validate NVENC parameters in automated tests where feasible.
- **Observability mindset** - Add structured logs, metrics, and alerts alongside new functionality.

## Standard workflow

1. **Intake & clarification**
   - Read existing issues/docs, reproduce the scenario locally when possible.
   - Ask for missing requirements early; never assume unsupported codecs or resolutions.
2. **Design**
   - Outline impact on architecture, volumes, and GPU usage.
   - Update diagrams/specs under `docs/` during the same change.
3. **Implementation**
   - Follow repo conventions (Python type hints, shell linting, Compose structure).
   - Keep changes minimal and focused; avoid unrelated formatting churn.
4. **Validation**
   - Write/extend automated tests (unit/integration) covering config parsing, job scheduling, and ffmpeg command generation.
   - Provide sample ffprobe outputs or fixtures for regression checks.
5. **Documentation & review prep**
   - Summarize changes, note risks, and record manual test commands.
   - Ensure CHANGELOG/release notes capture externally visible impact.

## Quality checklist per change

- [ ] Requirements traced to code/tests/docs.
- [ ] Config schema updated with defaults and validation errors for new fields.
- [ ] Telemetry/logging contains enough context (file path, job id, GPU id).
- [ ] Error handling is fault tolerant (retries, fallbacks) and surfaces actionable messages.
- [ ] Tests run locally (or within CI) and results recorded in the PR.
- [ ] Security review completed when touching networking, credentials, or bind mounts.

## Collaboration expectations

- Use feature branches and draft PRs for transparency.
- Keep commits logically scoped (docs-only, feature, refactor).
- When integrating with other agents/humans, leave notes on follow-up tasks and unresolved questions.
- Respect existing formatting (Black for Python, shfmt for shell, Markdown linting).

## Tooling guidance

- **Python** - Leverage `ruff`, `pytest`, and type checking (`mypy`). Prefer fastapi/typer for services/scripts.
- **Shell** - Use `shellcheck` and keep scripts POSIX-compatible when possible.
- **Containers** - Validate Dockerfiles with `hadolint`. Document base image choices and GPU runtime flags.
- **CI/CD** - Add/adjust GitHub Actions pipelines to run lint, tests, and security scans (trivy, syft).

## Escalation & rollback

- If GPU encoding fails or produces out-of-spec files, halt the pipeline by toggling the orchestrator feature flag and notify maintainers.
- Preserve original media until at least two verification steps pass.
- For critical regressions, prioritize minimal rollback patches coupled with post-mortem notes in `docs/incidents/`.
