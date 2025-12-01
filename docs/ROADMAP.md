# Roadmap and Gap Analysis

This roadmap tracks the current MVP state, known risks, and staged steps toward a
production-ready public release that could later bundle alongside Jellyfin.

Low-maintenance is the default posture: every stage below should reduce manual
touch points, minimize moving parts, and keep operational work predictable.

## Current MVP snapshot

- **Orchestrator API and dashboard** – FastAPI service exposes health/ready
  endpoints, queue listings, log streaming, manual scans, job claims/updates,
  runtime library add/remove (`/api/libraries`), websocket push channel (`/ws`),
  and an HTML dashboard with live updates.
- **Config-driven profiles** – Profiles are persisted in the SQLite config
  store (seeded from the built-in defaults in `app/config.py`) and validated for
  Chromecast-safe codec, profile, level, resolution, and bitrate limits before
  use. GPU-first with CPU fallback is modeled as a single `chromecast` profile.
- **Job ingestion and scans** – The orchestrator loads configured libraries at
  startup, runs recursive scans, ingests watcher events, and supports on-demand
  rescans. Library entries and job history are persisted for auditability.
- **GPU worker** – A polling worker claims jobs, builds FFmpeg commands for
  NVENC, streams progress back to the orchestrator, validates the resulting
  output, and optionally deletes the source after success.
- **Folder watcher** – An Alpine-based inotify loop streams create/modify/delete
  events (plus metadata) for each configured root; if the API is unreachable it
  spools batches to disk and replays them on restart.

## Gaps and risks

- **Configuration durability** – The SQLite config store lacks migration /
  backup tooling or export/import paths for upgrades or host moves.
- **Queue durability and scaling** – Redis-backed queue exists, but HA/backup
  guidance, visibility-timeout monitoring, and multi-worker fairness under load
  are not validated. No metrics on queue latency/retries yet.
- **Watcher dedupe** – Spool-to-disk prevents loss, but no dedupe layer exists
  when multiple watchers cover the same path; replay could enqueue duplicates.
- **Operational guardrails** – No enforcement of GPU temperature, disk-space
  thresholds, or dynamic concurrency throttling; metrics/alerting remain minimal.
- **Backups and auditability** – Library/catalog and job history are persisted
  but lack backup/retention guidance and user-facing export/audit tools. Jellyfin
  triggers still lack retry/confirmation hardening.
- **Jellyfin integration** – Triggering Jellyfin library refreshes requires the
  optional config block; there is no transport hardening, retry policy, or
  handshake to confirm the media server accepted the request.

### Low-maintenance alignment

- **Favor boring-by-default dependencies** – Redis (already provisioned) should
  be the only stateful service needed for queue durability. Optional pieces
  (database history, Jellyfin bundle) must be off by default and documented as
  add-ons.
- **Make desired behavior the default** – Persisted profiles, guardrails, and
  watcher behavior should be configured via the API and stick across restarts
  without manual file edits or ad hoc restarts.
- **Automate operator feedback** – Prefer metrics, alerts, and health signals
  over dashboards that require babysitting. When work cannot proceed (e.g., GPU
  constraints), the system should refuse the job and surface why.
- **Reduce heavy coordination** – Target a single orchestrator + worker path as
  the happy case. Scaling to many workers should remain possible, but not
  require more moving parts than Redis and optional metrics storage.

## Roadmap

### Hardening the core stack

1. **Persist configuration and job state**
    - Harden the SQLite configuration store with export/import paths and
      migrations so upgrades remain repeatable without manual edits.
    - Add backup/restore guidance for config, catalog, logs, and job history DBs.
   - Validate Redis durability/HA and visibility-timeout handling; add metrics
     for queue latency and retries to support multi-worker coordination.

2. **Honor profile inputs in FFmpeg commands**
   - Propagate profile tier and resolution into the FFmpeg builder, adjust the
     scaling expression accordingly, and add unit tests covering parameter
     derivation for each supported profile so manual smoke tests are not
     required.

3. **Improve change detection and ingestion**
   - Add cross-watcher dedupe/idempotency for replayed events (checksums or
     per-path sequence numbers) and document spool sizing/rotation guidance.

### Operational readiness

4. **Guardrails and observability**
   - Enforce concurrency, GPU temperature, and disk-usage limits from
     `operational` config; reject or pause jobs when thresholds are exceeded.
   - Expose Prometheus-compatible metrics (queue depth, job latency,
     FFmpeg success rate) and expand structured logging with request IDs and
     durable retention to minimize manual log wrangling.

5. **Quality and release process**
   - Add automated tests for config validation, queue lifecycle, FFmpeg command
     generation, and event ingestion. Gate PRs on ruff/black/test runs to keep
     regressions low-touch.
   - Publish versioned container images and document upgrade paths, backups,
     and rollback procedures for public users so maintenance is repeatable.

### Future Jellyfin bundle

6. **Integration and packaging**
   - Harden Jellyfin triggers with retries and clearer status reporting; make
     the integration optional by profile/library so users without Jellyfin are
     unaffected.
   - Prepare a compose profile that co-hosts a Jellyfin container alongside the
     orchestrator/worker stack in a future release, ensuring media mounts and
     networking are aligned.
