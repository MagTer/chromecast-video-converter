# Database migrations

The orchestrator and GPU worker now share a SQLite schema managed through Alembic. The
migration assets live under `services/orchestrator/alembic`, and both containers run
`alembic upgrade head` during startup using the configured database URL.

## Database URL

- Configure the database location with `DATABASE_URL`. SQLite URLs are supported out of
  the box. Defaults:
  - Orchestrator: `sqlite:////app/data/library.db`
  - GPU worker: `sqlite:////app/data/gpu-ffmpeg/worker.db`
- The compose stack mounts `./data` at `/app/data`, so both defaults persist across
  container rebuilds.

## Creating revisions

1. Ensure you have the Python requirements installed (see `services/orchestrator/requirements.txt`).
2. From the repository root, run Alembic with the shared configuration:
   ```bash
   alembic -c services/orchestrator/alembic.ini revision -m "short description"
   ```
3. Edit the generated file in `services/orchestrator/alembic/versions/` to include the
   desired schema changes. Autogeneration is available via `--autogenerate`, but the
   current project uses explicit operations for clarity.

## Applying migrations locally

- Apply all migrations with:
  ```bash
  alembic -c services/orchestrator/alembic.ini upgrade head
  ```
- The orchestrator and GPU worker entrypoints also run this command automatically during
  container startup to keep the schema current.

## Downgrade policy

- Downgrades exist for development convenience but should be avoided in shared or
  production environments.
- Prefer forward migrations; only run `alembic downgrade` to unblock local development,
  and never as part of normal operations.
