# Roadmap and Gap Analysis

This roadmap tracks the current MVP state, known risks, and staged steps toward a
production-ready public release that could later bundle alongside Jellyfin.

Low-maintenance is the default posture: every stage below should reduce manual
touch points, minimize moving parts, and keep operational work predictable.

## Current MVP snapshot

- **Orchestrator API and dashboard** – FastAPI service exposes health/ready
  endpoints, queue listings, log streaming, manual scans, job claims/updates,
  and an HTML dashboard that drives those APIs.
- **Config-driven profiles** – Profiles are loaded from `config/settings.yaml`
  (falling back to the sample) and validated for Chromecast-safe codec,
  profile, level, resolution, and bitrate limits before use.
- **Job ingestion and scans** – The orchestrator loads configured libraries at
  startup, runs a recursive scan to queue eligible files, and can rescan on
  demand via the API or a watcher-triggered event stream.
- **GPU worker** – A polling worker claims jobs, builds FFmpeg commands for
  NVENC, streams progress back to the orchestrator, validates the resulting
  output, and optionally deletes the source after success.
- **Folder watcher** – An Alpine-based loop requests scans for each configured
  root on a fixed interval so new or changed media is enqueued without manual
  action.

## Gaps and risks

- **Configuration persistence and application** – The dashboard updates the
  in-memory encoding profiles only; changes are not written back to
  `settings.yaml`, and several profile fields (resolution, H.264 profile tier)
  are ignored when constructing FFmpeg commands. Restarting the orchestrator or
  worker discards GUI edits.
- **Queue durability and scaling** – Jobs are stored in an in-memory manager;
  Redis is deployed but unused. Orchestrator restarts wipe the queue, and
  multiple GPU workers cannot safely coordinate job claims.
- **Watcher fidelity** – The watcher issues full library scans on a timer rather
  than streaming file system events, leading to latency and repeated work.
  Missed or partial scans are not retried.
- **Operational guardrails** – There is no enforcement of GPU temperature,
  disk-space thresholds, or concurrency limits beyond the static FFmpeg
  invocation. Metrics and alerting are absent.
- **Catalog state and auditability** – Job metadata lives only in memory; there
  is no persistent catalog of what was processed, why failures occurred, or
  what changes operators applied. Log retention is bounded to a small in-memory
  buffer.
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
    - Add config-write support so `/api/config/encoding` updates both the
      in-memory model and `config/settings.yaml`, with validation and rollback on
     failure to avoid manual repairs.
   - Back the job queue with Redis (already provisioned) and store job history
     in SQLite/PostgreSQL to survive orchestrator restarts and enable
     multi-worker coordination without operator intervention.

2. **Honor profile inputs in FFmpeg commands**
   - Propagate profile tier and resolution into the FFmpeg builder, adjust the
     scaling expression accordingly, and add unit tests covering parameter
     derivation for each supported profile so manual smoke tests are not
     required.

3. **Improve change detection and ingestion**
   - Replace the timed full-scan watcher with an inotify-based event stream
     that sends create/modify/delete signals to the orchestrator. Add minimal
     backoff/retry behavior when the orchestrator is unavailable so resyncs are
     automatic.

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
