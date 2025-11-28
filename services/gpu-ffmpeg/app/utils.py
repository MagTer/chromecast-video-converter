import os
from pathlib import Path

WATCH_PREFIX = Path("/watch")
MEDIA_PREFIX = Path("/media")


def resolve_media_path(path: str | Path) -> Path:
    normalized = Path(str(path).replace("\\", "/"))
    try:
        relative = normalized.relative_to(WATCH_PREFIX)
    except ValueError:
        return normalized
    return MEDIA_PREFIX / relative


def detect_host_environment() -> dict[str, bool]:
    """Detect whether we're running under WSL2 (for NVENC quirks)."""
    try:
        version = Path("/proc/version").read_text().lower()
        if "microsoft" in version or "wsl2" in version:
            return {"is_wsl2": True}
    except OSError:
        pass

    is_wsl2 = any(os.environ.get(var) for var in ("WSL_DISTRO_NAME", "WSL_INTEROP"))
    return {"is_wsl2": is_wsl2}
