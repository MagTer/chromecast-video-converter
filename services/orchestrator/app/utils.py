import os
from pathlib import Path
from typing import Optional, Union

# Constants
DISPLAY_LIBRARY_PREFIX = os.environ.get("DISPLAY_LIBRARY_PREFIX", "/media").rstrip("/")
LIBRARY_ROOT_PREFIXES = [
    Path(prefix.strip())
    for prefix in os.environ.get("LIBRARY_ROOT_PREFIXES", "/watch,/media").split(",")
    if prefix.strip()
]

PathInput = Union[str, Path]


def _stringify_path(path: Optional[PathInput]) -> Optional[str]:
    if path is None:
        return None
    if isinstance(path, Path):
        return path.as_posix()
    return str(path)


def detect_wsl2() -> bool:
    try:
        version = Path("/proc/version").read_text().lower()
        if "microsoft" in version or "wsl2" in version:
            return True
    except OSError:
        pass
    return any(os.environ.get(var) for var in ("WSL_DISTRO_NAME", "WSL_INTEROP"))


def normalize_display_path(path: Optional[PathInput]) -> Optional[str]:
    raw = _stringify_path(path)
    if raw is None:
        return None
    normalized = raw.replace("\\", "/")
    canonical_prefix = DISPLAY_LIBRARY_PREFIX
    for prefix in LIBRARY_ROOT_PREFIXES:
        prefix_str = prefix.as_posix().rstrip("/")
        if not prefix_str:
            continue
        if normalized == prefix_str or normalized == f"{prefix_str}/":
            return canonical_prefix or prefix_str
        match_prefix = f"{prefix_str}/"
        if normalized.startswith(match_prefix):
            suffix = normalized[len(prefix_str) :]
            target_prefix = canonical_prefix or prefix_str
            rebuilt = f"{target_prefix}{suffix}"
            return rebuilt.replace("//", "/")
    return normalized


def resolve_media_path(path: Optional[PathInput]) -> Path:
    normalized = normalize_display_path(path)
    target = normalized or _stringify_path(path)
    if not target:
        raise ValueError("Path is required")
    return Path(target)
