# 02 - Configuration Guidelines

## Hard-coded directory mappings

- The host directories that feed media files into the pipeline are bound directly in `docker-compose.yml` through explicit volume mounts (e.g., `D:/Media/Movies` → `/watch/movies`). These mount points are stable and should not be changed via the UI; if you need to point to a different host path you must edit the Compose service volume, not the orchestrator config.
- Because these host-root bindings determine what the containers actually see, the orchestrator’s library definitions inside `config/settings.yaml` must use the corresponding Linux path (`/watch/movies`, `/watch/series`, `/media/...`) while the Windows host path stays locked to the left-hand side of the Compose mounts.

`config/settings.yaml.template` is solely a starter copy that you duplicate when onboarding the stack. The orchestrator and GPU worker read and persist `config/settings.yaml` (the file you edit or the GUI modifies), so leave the template untouched once the stack is configured.

## GUI-powered tuning

- The orchestrator dashboard & API accept JSON/YAML that controls library names, profiles, bitrates, and Jellyfin integration. Those fields are surfaced through the GUI so operators can tune quality and automation; they do not change the host path mappings.
- When a GUI change adds a new library, ensure its `root` matches one of the existing mount points (e.g., `root: /media/movies`), otherwise the files will not be reachable.
- Jellyfin integration is optional; omit the `jellyfin` section from `config/settings.yaml` (as shown in `config/settings.yaml.template`) whenever no server is reachable, and the orchestrator will quietly skip those refresh tasks.
- Log retention is also editable in the GUI. The `logging.retention_days` key in `config/settings.yaml` (default: `7`) controls how long centralized logs from every container stay on disk. The Configuration page displays current disk usage for the log database mounted at `./logs`.

## Keeping configs aligned

- After editing `docker-compose.yml` to point to different host folders, restart the stack to refresh the mounts.
- If you add new directories in Compose later, update `config/settings.yaml` (or the GUI) to include the new library/profile pair so the orchestrator knows how to schedule jobs there.
