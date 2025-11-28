import os
from pathlib import Path
from typing import Optional

# Constants
DISPLAY_LIBRARY_PREFIX = "/media"
LIBRARY_ROOT_PREFIXES = [
    Path(prefix.strip())
    for prefix in os.environ.get("LIBRARY_ROOT_PREFIXES", "/watch,/media").split(",")
    if prefix.strip()
]


def detect_wsl2() -> bool:
    try:
        version = Path("/proc/version").read_text().lower()
        if "microsoft" in version or "wsl2" in version:
            return True
    except OSError:
        pass
    return any(os.environ.get(var) for var in ("WSL_DISTRO_NAME", "WSL_INTEROP"))


def normalize_display_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return path
    normalized = path.replace("\\", "/")
    watch_prefix = "/watch/"
    if normalized == "/watch":
        return DISPLAY_LIBRARY_PREFIX
    if normalized.startswith(watch_prefix):
        suffix = normalized[len(watch_prefix) :]
        return f"{DISPLAY_LIBRARY_PREFIX.rstrip('/')}/{suffix}".replace("//", "/")
    return normalized


def resolve_media_path(path: Optional[str]) -> Path:
    normalized = normalize_display_path(path)
    target = normalized or path
    if not target:
        raise ValueError("Path is required")
    return Path(target)
