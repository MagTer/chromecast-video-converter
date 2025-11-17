# 02 - Configuration Guidelines

## Media paths via `.env`

- Copy `.env.template` to `.env` and set `PATH_MOVIES`/`PATH_SERIES` to the host directories that hold your libraries. Relative values are resolved from the repository root (for example, `./media/movies`); absolute paths work for network shares or mounted drives such as `/mnt/storage/Movies` or `D:\\Media\\Movies` on Windows.
- Docker Compose consumes those variables in every service, binding each host directory twice: once to `/watch/<library>` and once to `/media/<library>`. The orchestrator understands both mount roots, so UI/API calls and watcher events can reference either prefix.
- Because these host-root bindings determine what the containers actually see, the orchestrator’s library definitions inside `config/settings.yaml` must use one of the mounted Linux paths (`/watch/movies`, `/watch/series`, `/media/...`) while the Windows host path stays locked to the left-hand side of the Compose mounts.

`config/settings.yaml.template` is solely a starter copy that you duplicate when onboarding the stack. The orchestrator and GPU worker read and persist `config/settings.yaml` (the file you edit or the GUI modifies), so leave the template untouched once the stack is configured.

The Compose stack now mounts `./config` into both the orchestrator and GPU worker with write access so GUI changes to encoding presets are flushed back to disk. Keep the directory under version control to track edits.

## GUI-powered tuning

- The orchestrator dashboard & API accept JSON/YAML that controls library names, profiles, bitrates, and Jellyfin integration. Those fields are surfaced through the GUI so operators can tune quality and automation; they do not change the host path mappings.
- Encoding controls are provided as dropdowns tuned for Chromecast Gen 2/3: NVENC presets (p1–p7), rate control modes (VBR HQ, VBR, CBR), CQ targets, max bitrates/buffers, and the 24–30 fps cap. Audio is always transcoded to AAC stereo (2 channels) with selectable bitrates, and all source tracks are preserved.
- When a GUI change adds a new library, ensure its `root` matches one of the existing mount points (e.g., `root: /media/movies`), otherwise the files will not be reachable.
- Jellyfin integration is optional; omit the `jellyfin` section from `config/settings.yaml` (as shown in `config/settings.yaml.template`) whenever no server is reachable, and the orchestrator will quietly skip those refresh tasks.
- Log retention is also editable in the GUI. The `logging.retention_days` key in `config/settings.yaml` (default: `7`) controls how long centralized logs from every container stay on disk. The Configuration page displays current disk usage for the log database mounted at `./logs`.

## Keeping configs aligned

- After editing `docker-compose.yml` to point to different host folders, restart the stack to refresh the mounts.
- If you add new directories in Compose later, update `config/settings.yaml` (or the GUI) to include the new library/profile pair so the orchestrator knows how to schedule jobs there.
