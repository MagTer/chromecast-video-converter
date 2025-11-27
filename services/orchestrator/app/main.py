from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import sqlalchemy
from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import config as config_module
from . import jellyfin, jobs
from .db import Base, create_session_factory
from .job_history import JobHistoryEntry, JobHistoryStatus, JobHistoryStore
from .library_entries import EntryUpdate, LibraryEntry, LibraryEntryStore, LibraryStatus
from .logs import (
    LogEntry,
    LogStore,
    SQLiteLogHandler,
    StructuredLogFilter,
    derive_source_category,
    severity_value,
)
from .profiles import (
    EncodingProfile,
    LibraryConfig,
    LibraryConfigStore,
    LibraryData,
    ProfileData,
    ProfileStore,
)

logging.addLevelName(logging.DEBUG, "VERBOSE")


def _resolve_data_dir() -> Path:
    default = Path(os.environ.get("DATA_DIR", "/app/data")).resolve()
    try:
        default.mkdir(parents=True, exist_ok=True)
        return default
    except OSError:
        fallback = Path("./data").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR = _resolve_data_dir()

LOG_DB_PATH = Path(os.environ.get("LOG_DB_PATH", DATA_DIR / "events.db")).resolve()
CONFIG_DB_PATH = Path(os.environ.get("CONFIG_DB_PATH", DATA_DIR / "config.db")).resolve()
CONFIG_TEMPLATE_PATH = Path(
    os.environ.get("CONFIG_TEMPLATE_PATH", "/app/config/settings.yaml.template")
).resolve()
LEGACY_CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/settings.yaml")).resolve()

LOG_STORE = LogStore(LOG_DB_PATH)
LIBRARY_DB_PATH = Path(os.environ.get("LIBRARY_DB_PATH", DATA_DIR / "library.db")).resolve()
SESSION_FACTORY, ENGINE = create_session_factory(LIBRARY_DB_PATH)
PROFILE_STORE = ProfileStore(SESSION_FACTORY)
LIBRARY_CONFIG_STORE = LibraryConfigStore(SESSION_FACTORY)
LIBRARY_STORE = LibraryEntryStore(LIBRARY_DB_PATH, session_factory=SESSION_FACTORY, engine=ENGINE)
JOB_HISTORY_STORE = JobHistoryStore(SESSION_FACTORY)
Base.metadata.create_all(ENGINE)
LIBRARY_STATUSES = {
    LibraryStatus.PENDING,
    LibraryStatus.CONVERTING,
    LibraryStatus.CONVERTED,
    LibraryStatus.FAILED,
    LibraryStatus.REMOVED,
}
LIBRARY_ROOT_PREFIXES = [
    Path(prefix.strip())
    for prefix in os.environ.get("LIBRARY_ROOT_PREFIXES", "/watch,/media").split(",")
    if prefix.strip()
]
DISPLAY_LIBRARY_PREFIX = "/media"
WORKER_TELEMETRY: Dict[str, "WorkerTelemetryPayload"] = {}


def _detect_wsl2() -> bool:
    """Detect WSL2 using kernel markers or well-known environment variables."""

    try:
        version = Path("/proc/version").read_text().lower()
        if "microsoft" in version or "wsl2" in version:
            return True
    except OSError:
        pass

    return any(os.environ.get(var) for var in ("WSL_DISTRO_NAME", "WSL_INTEROP"))


HOST_ENVIRONMENT = {"is_wsl2": _detect_wsl2()}


def _ensure_schema_revision(engine) -> None:
    """Auto-upgrade schema if critical NVENC columns are missing.

    This makes the container self-healing when a mounted DB predates the
    latest migration; it runs alembic against the same URL the ORM uses.
    """

    required_columns = {
        "codec",
        "definition",
        "profile_tier",
        "max_resolution",
        "bitrate",
        "max_bitrate",
        "bufsize",
        "preset",
        "cq",
        "rc",
        "level",
        "max_fps",
        "bframes",
        "lookahead",
        "adaptive_b_frames",
        "aq",
        "spatial_aq",
        "temporal_aq",
        "aq_strength",
        "audio_codec",
        "audio_bitrate",
        "audio_channels",
    }
    with engine.connect() as conn:
        rows = conn.execute(sqlalchemy.text("PRAGMA table_info('encoding_profiles')")).fetchall()
    present = {row[1] for row in rows}
    missing = required_columns - present
    if not missing:
        return

    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str((Path(__file__).parent / ".." / "alembic.ini").resolve()))
        cfg.set_main_option("sqlalchemy.url", str(engine.url))
        command.upgrade(cfg, "head")
    except Exception:  # noqa: BLE001
        missing = required_columns  # fallback to manual patching below

    # Re-check after migration
    with engine.connect() as conn:
        rows = conn.execute(sqlalchemy.text("PRAGMA table_info('encoding_profiles')")).fetchall()
    present = {row[1] for row in rows}
    missing = required_columns - present
    if missing:
        # Manual patch for stale DBs that report head but lack columns
        alter_statements = {
            "codec": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "codec TEXT NOT NULL DEFAULT 'h264'"
            ),
            "definition": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "definition TEXT NOT NULL DEFAULT '{}'"
            ),
            "profile_tier": (
                "ALTER TABLE encoding_profiles ADD COLUMN "
                "profile_tier TEXT NOT NULL DEFAULT 'high'"
            ),
            "max_resolution": (
                "ALTER TABLE encoding_profiles ADD COLUMN "
                "max_resolution TEXT NOT NULL DEFAULT '1280x720'"
            ),
            "bitrate": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "bitrate TEXT NOT NULL DEFAULT '8M'"
            ),
            "max_bitrate": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "max_bitrate TEXT NOT NULL DEFAULT '8M'"
            ),
            "bufsize": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "bufsize TEXT NOT NULL DEFAULT '16M'"
            ),
            "preset": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "preset TEXT NOT NULL DEFAULT 'p6'"
            ),
            "cq": "ALTER TABLE encoding_profiles ADD COLUMN cq INTEGER NOT NULL DEFAULT 18",
            "rc": "ALTER TABLE encoding_profiles ADD COLUMN rc TEXT NOT NULL DEFAULT 'vbr_hq'",
            "level": "ALTER TABLE encoding_profiles ADD COLUMN level TEXT NOT NULL DEFAULT '4.1'",
            "max_fps": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "max_fps INTEGER NOT NULL DEFAULT 30"
            ),
            "bframes": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "bframes INTEGER NOT NULL DEFAULT 2"
            ),
            "lookahead": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "lookahead INTEGER NOT NULL DEFAULT 24"
            ),
            "adaptive_b_frames": (
                "ALTER TABLE encoding_profiles ADD COLUMN "
                "adaptive_b_frames BOOLEAN NOT NULL DEFAULT 1"
            ),
            "aq": ("ALTER TABLE encoding_profiles ADD COLUMN aq BOOLEAN NOT NULL DEFAULT 1"),
            "spatial_aq": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "spatial_aq BOOLEAN NOT NULL DEFAULT 1"
            ),
            "temporal_aq": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "temporal_aq BOOLEAN NOT NULL DEFAULT 1"
            ),
            "aq_strength": (
                "ALTER TABLE encoding_profiles ADD COLUMN " "aq_strength INTEGER NOT NULL DEFAULT 7"
            ),
            "audio_codec": (
                "ALTER TABLE encoding_profiles ADD COLUMN "
                "audio_codec TEXT NOT NULL DEFAULT 'aac'"
            ),
            "audio_bitrate": (
                "ALTER TABLE encoding_profiles ADD COLUMN "
                "audio_bitrate TEXT NOT NULL DEFAULT '192k'"
            ),
            "audio_channels": (
                "ALTER TABLE encoding_profiles ADD COLUMN "
                "audio_channels INTEGER NOT NULL DEFAULT 2"
            ),
        }
        with engine.begin() as conn:
            for column in missing:
                stmt = alter_statements.get(column)
                if stmt:
                    conn.execute(sqlalchemy.text(stmt))

        # Final check
        with engine.connect() as conn:
            rows = conn.execute(
                sqlalchemy.text("PRAGMA table_info('encoding_profiles')")
            ).fetchall()
        present = {row[1] for row in rows}
        remaining = required_columns - present
        if remaining:
            details = ", ".join(sorted(remaining))
            raise RuntimeError(
                "Config DB schema is still missing columns after manual patch: "
                f"{details}. Please inspect the database file or run alembic manually."
            )


_ensure_schema_revision(ENGINE)


class WebsocketNotifier:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        payload = jsonable_encoder(message)
        async with self._lock:
            targets = list(self._connections)
        if not targets:
            return

        async def _send(ws: WebSocket) -> None:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect(ws)

        await asyncio.gather(*(_send(ws) for ws in targets), return_exceptions=True)


def configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    sqlite_handler = SQLiteLogHandler(LOG_STORE)
    sqlite_handler.setLevel(logging.DEBUG)
    sqlite_handler.setFormatter(formatter)
    context_filter = StructuredLogFilter()
    stream_handler.addFilter(context_filter)
    sqlite_handler.addFilter(context_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addFilter(context_filter)
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(sqlite_handler)


configure_logging()

LOGGER = logging.getLogger("orchestrator")
NOTIFIER = WebsocketNotifier()

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"
INDEX_HTML = TEMPLATE_PATH.read_text()

app = FastAPI(title="Chromecast Transcode Orchestrator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ScanRequest(BaseModel):
    library: Optional[str] = None
    root: Optional[str] = None


class EventPayload(BaseModel):
    path: str
    library: Optional[str] = None
    event: str = Field(default="created")
    size: Optional[int] = None
    modified_at: Optional[datetime] = None
    is_directory: bool = False


class EventBatch(BaseModel):
    events: List[EventPayload]


class JobStatusPayload(BaseModel):
    status: str
    progress: Optional[int] = None
    message: Optional[str] = None


class JobAckPayload(BaseModel):
    delivery_id: str


class QueuePauseRequest(BaseModel):
    reason: Optional[str] = None


class ReprocessPayload(BaseModel):
    profile_id: Optional[int] = None


class LibraryProfilePayload(BaseModel):
    profile_id: int


class EntryProfilePayload(BaseModel):
    profile_id: int


class LibraryCreatePayload(BaseModel):
    name: str = Field(min_length=1)
    root: str = Field(min_length=1, description="Absolute path to the library root")
    depth: Optional[str] = Field(default=None)
    profile_id: int


class WorkerTelemetryPayload(BaseModel):
    worker_id: str
    gpu_available: bool
    devices: List[str] = Field(default_factory=list)
    cuda_available: bool
    nvenc_available: bool
    checked_at: datetime
    message: Optional[str] = None


def _worker_metrics_summary() -> Dict[str, Any]:
    payloads = list(WORKER_TELEMETRY.values())
    telemetry = [jsonable_encoder(item) for item in payloads]
    available = sum(1 for item in payloads if item.gpu_available)
    return {"workers": len(payloads), "available": available, "telemetry": telemetry}


async def _emit_library_update(action: str, payload: Dict[str, Any]) -> None:
    await NOTIFIER.broadcast({"type": "library-update", "action": action, "library": payload})


def _normalize_display_path(path: Optional[str]) -> Optional[str]:
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


def _resolve_media_path(path: Optional[str]) -> Path:
    normalized = _normalize_display_path(path)
    target = normalized or path
    if not target:
        raise ValueError("Path is required")
    return Path(target)


class LibraryEntryResponse(BaseModel):
    id: int
    path: str
    library: str
    profile: str
    profile_id: Optional[int] = None
    status: str
    output_path: Optional[str] = None
    last_error: Optional[str] = None
    last_job_id: Optional[str] = None
    original_missing: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True, json_encoders={datetime: lambda value: value.isoformat()}
    )


class EncodingUpdatePayload(BaseModel):
    name: str = Field(description="Profile name to upsert")
    codec: str
    profile: str
    level: str
    resolution: str
    max_fps: int = Field(default=30, gt=0, le=60)
    bitrate: str = Field(default="8M")
    max_bitrate: str
    bufsize: str
    preset: str
    cq: int = Field(ge=0, le=30)
    rc: str
    bframes: int = Field(default=2, ge=0, le=3)
    lookahead: int = Field(default=24, ge=0, le=32)
    adaptive_b_frames: bool = Field(default=True)
    aq: bool = Field(default=True)
    spatial_aq: bool = Field(default=True)
    temporal_aq: bool = Field(default=True)
    aq_strength: int = Field(default=7, ge=5, le=10)
    audio: config_module.AudioProfile


class LoggingUpdatePayload(BaseModel):
    retention_days: int = Field(ge=1, le=90)


class LogIngestEvent(BaseModel):
    logger: str
    level: str
    message: str
    severity: Optional[str] = None
    source: Optional[str] = None
    category: Optional[str] = None
    timestamp: Optional[datetime] = None


class LogIngestBatch(BaseModel):
    entries: List[LogIngestEvent]


def _cache_headers(snapshot: config_module.ConfigSnapshot) -> Dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Config-Revision": str(snapshot.revision),
    }


def _seed_profiles_and_libraries(snapshot: config_module.ConfigSnapshot) -> None:
    name_to_id: Dict[str, int] = {}
    for name, profile in snapshot.config.profiles.items():
        record = PROFILE_STORE.upsert(
            ProfileData(
                name=name,
                codec=profile.codec,
                profile_tier=profile.profile,
                max_resolution=profile.resolution,
                bitrate=profile.bitrate,
                max_bitrate=profile.max_bitrate,
                bufsize=profile.bufsize,
                preset=profile.preset,
                cq=profile.cq,
                rc=profile.rc,
                level=profile.level,
                max_fps=profile.max_fps,
                bframes=profile.bframes,
                lookahead=profile.lookahead,
                adaptive_b_frames=profile.adaptive_b_frames,
                aq=profile.aq,
                spatial_aq=profile.spatial_aq,
                temporal_aq=profile.temporal_aq,
                aq_strength=getattr(profile, "aq_strength", 7),
                audio_codec=profile.audio.codec,
                audio_bitrate=profile.audio.bitrate,
                audio_channels=profile.audio.channels,
            )
        )
        name_to_id[name] = record.id

    for name, library in snapshot.config.libraries.items():
        profile_id = library.profile_id
        if profile_id is None or PROFILE_STORE.get(profile_id) is None:
            profile_id = name_to_id.get(library.profile)
        if profile_id is None:
            LOGGER.warning(
                "Skipping library %s because profile %s was not seeded", name, library.profile
            )
            continue
        LIBRARY_CONFIG_STORE.upsert(
            LibraryData(
                name=name,
                root=library.root,
                depth=library.depth,
                profile_id=profile_id,
            )
        )


def _profile_data_from_payload(
    payload: EncodingUpdatePayload,
) -> tuple[ProfileData, config_module.Profile]:
    validated = config_module.Profile(
        codec=payload.codec,
        profile=payload.profile,
        level=payload.level,
        resolution=payload.resolution,
        max_fps=payload.max_fps,
        bitrate=payload.bitrate,
        max_bitrate=payload.max_bitrate,
        bufsize=payload.bufsize,
        preset=payload.preset,
        cq=payload.cq,
        rc=payload.rc,
        bframes=payload.bframes,
        lookahead=payload.lookahead,
        adaptive_b_frames=payload.adaptive_b_frames,
        aq=payload.aq,
        spatial_aq=payload.spatial_aq,
        temporal_aq=payload.temporal_aq,
        aq_strength=payload.aq_strength,
        audio=payload.audio,
    )
    profile_data = ProfileData(
        name=payload.name,
        codec=validated.codec,
        definition="{}",
        profile_tier=validated.profile,
        max_resolution=validated.resolution,
        bitrate=validated.bitrate,
        max_bitrate=validated.max_bitrate,
        bufsize=validated.bufsize,
        preset=validated.preset,
        cq=validated.cq,
        rc=validated.rc,
        level=validated.level,
        max_fps=validated.max_fps,
        bframes=validated.bframes,
        lookahead=validated.lookahead,
        adaptive_b_frames=validated.adaptive_b_frames,
        aq=validated.aq,
        spatial_aq=validated.spatial_aq,
        temporal_aq=validated.temporal_aq,
        aq_strength=validated.aq_strength,
        audio_codec=validated.audio.codec,
        audio_bitrate=validated.audio.bitrate,
        audio_channels=validated.audio.channels,
    )
    return profile_data, validated


config_service = config_module.ConfigService(
    CONFIG_DB_PATH, CONFIG_TEMPLATE_PATH, LEGACY_CONFIG_PATH
)
config_snapshot = config_service.reload()
_seed_profiles_and_libraries(config_snapshot)
LOG_STORE.update_retention(config_snapshot.config.logging.retention_days)
JOB_QUEUE_URL = os.environ.get("JOB_QUEUE", "redis://localhost:6379/0")
JOB_VISIBILITY_TIMEOUT = int(os.environ.get("JOB_VISIBILITY_TIMEOUT", "300"))
job_manager = jobs.JobManager(JOB_QUEUE_URL, visibility_timeout=JOB_VISIBILITY_TIMEOUT)


def encoding_payload(profile_id: int) -> Dict[str, Any]:
    profile = PROFILE_STORE.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {
        "codec": profile.codec,
        "profile": profile.profile_tier,
        "resolution": profile.max_resolution,
        "max_resolution": profile.max_resolution,
        "bitrate": profile.bitrate,
        "max_bitrate": profile.max_bitrate,
        "bufsize": profile.bufsize,
        "preset": profile.preset,
        "cq": profile.cq,
        "rc": profile.rc,
        "level": profile.level,
        "max_fps": profile.max_fps,
        "bframes": profile.bframes,
        "lookahead": profile.lookahead,
        "adaptive_b_frames": profile.adaptive_b_frames,
        "aq": profile.aq,
        "aq_strength": getattr(profile, "aq_strength", 7),
        "spatial_aq": profile.spatial_aq,
        "temporal_aq": profile.temporal_aq,
        "audio": {
            "codec": profile.audio_codec,
            "bitrate": profile.audio_bitrate,
            "channels": profile.audio_channels,
        },
    }


def _resolve_relaxed(path: Path) -> Path:
    try:
        return path.resolve()
    except FileNotFoundError:
        return path.resolve(strict=False)


def _candidate_library_roots(root: Path) -> List[Path]:
    matches: List[Path] = []
    resolved_root = _resolve_relaxed(root)
    for prefix in LIBRARY_ROOT_PREFIXES:
        resolved_prefix = _resolve_relaxed(prefix)
        try:
            suffix = resolved_root.relative_to(resolved_prefix)
        except ValueError:
            continue
        for alt_prefix in LIBRARY_ROOT_PREFIXES:
            candidate = _resolve_relaxed(alt_prefix / suffix)
            if candidate not in matches:
                matches.append(candidate)
    if resolved_root not in matches:
        matches.append(resolved_root)
    return matches


def _should_track_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in job_manager.video_extensions
        and "-chromecast" not in path.stem.lower()
    )


def _library_map() -> Dict[str, LibraryConfig]:
    return {library.name: library for library in LIBRARY_CONFIG_STORE.list_libraries()}


def _library_profile(library_name: str) -> tuple[LibraryConfig, EncodingProfile]:
    library = LIBRARY_CONFIG_STORE.get(library_name)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    profile = PROFILE_STORE.get(library.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found for library")
    return library, profile


def _entry_status_for_path(path: Path) -> tuple[str, Path, bool]:
    output_path = job_manager.output_path(path)
    if not path.exists():
        return LibraryStatus.REMOVED, output_path, False
    if job_manager.is_converted(path, log=False):
        return LibraryStatus.CONVERTED, output_path, True
    return LibraryStatus.PENDING, output_path, True


async def _record_library_entry(
    library_name: str,
    path: Path,
    profile: str,
    profile_id: int,
    *,
    emit_log: bool = True,
) -> tuple[LibraryEntry, Optional[jobs.Job]]:
    status, output_path, original_exists = _entry_status_for_path(path)
    entry = LIBRARY_STORE.upsert(
        EntryUpdate(
            path=str(path),
            library=library_name,
            profile=profile,
            profile_id=profile_id,
            status=status,
            output_path=str(output_path),
            original_missing=not original_exists,
        ),
    )
    if status == LibraryStatus.PENDING:
        job = await job_manager.add_job(
            str(path),
            library_name,
            profile,
            profile_id=profile_id,
            encoding=encoding_payload(profile_id),
            emit_log=emit_log,
        )
        LIBRARY_STORE.attach_job(entry.id, job.id)
        _record_job_history(job, JobHistoryStatus.PENDING)
        return entry, job
    return entry, None


def _entry_to_response(entry: LibraryEntry) -> Dict[str, Any]:
    payload = LibraryEntryResponse.model_validate(entry).model_dump()
    payload["path"] = _normalize_display_path(payload.get("path"))
    if payload.get("output_path"):
        payload["output_path"] = _normalize_display_path(payload.get("output_path"))
    return payload


def _job_elapsed_seconds(job: jobs.Job) -> int:
    start = job.created_at or datetime.utcnow()
    if job.status in {jobs.JobStatus.COMPLETED, jobs.JobStatus.FAILED}:
        end = job.updated_at or datetime.utcnow()
    else:
        end = datetime.utcnow()
    try:
        return max(0, int((end - start).total_seconds()))
    except Exception:  # noqa: BLE001
        return 0


def _job_to_response(job: jobs.Job) -> Dict[str, Any]:
    payload = job.model_dump()
    payload["path"] = _normalize_display_path(payload.get("path"))
    payload["elapsed_seconds"] = _job_elapsed_seconds(job)
    return payload


def _sync_entry_from_job(job: jobs.Job, status: str, message: Optional[str] = None) -> None:
    source = Path(job.path)
    original_missing = not source.exists()
    final_status = status
    if status == LibraryStatus.CONVERTED and original_missing:
        final_status = LibraryStatus.REMOVED
    LIBRARY_STORE.safe_update_status(
        job.path,
        final_status,
        library=job.library,
        profile=job.profile,
        profile_id=job.profile_id,
        message=message,
        job_id=job.id,
        output_path=str(job_manager.output_path(source)),
        original_missing=original_missing,
    )


def _record_job_history(
    job: jobs.Job, status: str, message: Optional[str] = None, *, completed: bool = False
) -> None:
    completed_at = datetime.utcnow() if completed else None
    try:
        JOB_HISTORY_STORE.record(
            JobHistoryEntry(
                job_id=job.id,
                path=job.path,
                library=job.library,
                profile=job.profile,
                status=status,
                message=message,
                started_at=job.created_at,
                completed_at=completed_at,
            )
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Failed to persist job history for %s: %s", job.id[:8], exc)


def _get_entry_or_404(entry_id: int) -> LibraryEntry:
    entry = LIBRARY_STORE.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Library entry not found")
    return entry


async def _process_event_payload(payload: EventPayload) -> Optional[Dict[str, Any]]:
    event_type = payload.event.lower()
    if event_type not in {"created", "modified", "deleted"}:
        raise HTTPException(status_code=400, detail="Unsupported event type")
    library_name = payload.library or find_library_for_path(payload.path)
    if not library_name:
        raise HTTPException(status_code=400, detail="Library could not be determined")
    library, profile = _library_profile(library_name)

    path = Path(payload.path)
    if payload.is_directory:
        LOGGER.debug("Ignoring directory event for %s", path)
        return None

    if event_type == "deleted":
        entry = LIBRARY_STORE.update_status(
            str(path),
            LibraryStatus.REMOVED,
            library=library.name,
            profile=profile.name,
            profile_id=profile.id,
            output_path=str(job_manager.output_path(path)),
            original_missing=True,
        )
        return {"entry": _entry_to_response(entry), "event": event_type}

    if not _should_track_file(path):
        LOGGER.debug("Ignoring non-media event for %s", path)
        return None

    try:
        entry, job = await _record_library_entry(library.name, path, profile.name, profile.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    response: Dict[str, Any] = {"entry": _entry_to_response(entry), "event": event_type}
    if job:
        response["job"] = job.model_dump()
    return response


async def reconcile_library(library_name: str, root: str, profile: str, profile_id: int) -> None:
    root_path = Path(root)
    if not root_path.exists():
        LOGGER.warning("Library root %s missing; marking entries removed", root_path)
        LIBRARY_STORE.mark_missing(library_name, set())
        return

    seen: Set[str] = set()
    queued = 0
    converted = 0
    tracked_pending = 0
    for entry in root_path.rglob("*.*"):
        if not _should_track_file(entry):
            continue
        seen.add(str(entry))
        library_entry, job = await _record_library_entry(
            library_name, entry, profile, profile_id, emit_log=False
        )
        if job:
            queued += 1
        elif library_entry.status == LibraryStatus.CONVERTED:
            converted += 1
        elif library_entry.status == LibraryStatus.PENDING:
            tracked_pending += 1
    removed = LIBRARY_STORE.mark_missing(library_name, seen)
    LOGGER.info(
        (
            "Completed scan for %s: %s media files, queued %s, %s already converted, "
            "%s already tracked, %s removed"
        ),
        library_name,
        len(seen),
        queued,
        converted,
        tracked_pending,
        removed,
    )


def find_library_for_path(path: str) -> Optional[str]:
    try:
        normalized = Path(path).resolve()
    except FileNotFoundError:
        normalized = Path(path)
    for name, library in _library_map().items():
        library_root = Path(library.root)
        for candidate_root in _candidate_library_roots(library_root):
            if normalized.is_relative_to(candidate_root):
                return name
    return None


@app.on_event("startup")
async def startup_event() -> None:
    await job_manager.initialize()
    LOGGER.info("Starting initial scan of configured libraries.")
    for library in LIBRARY_CONFIG_STORE.list_libraries():
        profile = PROFILE_STORE.get(library.profile_id)
        if profile is None:
            LOGGER.warning("Library %s has missing profile id %s", library.name, library.profile_id)
            continue
        LOGGER.info("Scanning library %s at %s", library.name, library.root)
        await reconcile_library(library.name, library.root, profile.name, profile.id)

    jellyfin_cfg = config_service.snapshot.config.jellyfin
    if jellyfin_cfg:
        LOGGER.info("Scheduling Jellyfin scans for configured libraries.")
        asyncio.create_task(_safe_jellyfin_trigger(jellyfin_cfg))


async def _safe_jellyfin_trigger(jellyfin_cfg: config_module.JellyfinConfig) -> None:
    try:
        await jellyfin.trigger_all(jellyfin_cfg.libraries, jellyfin_cfg.url, jellyfin_cfg.api_key)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Jellyfin refresh failed", exc_info=exc)


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/api/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "libraries": len(_library_map())})


@app.get("/api/readyz")
async def readyz() -> JSONResponse:
    return JSONResponse({"status": "ready"})


@app.websocket("/ws")
async def websocket_updates(websocket: WebSocket) -> None:
    await NOTIFIER.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await NOTIFIER.disconnect(websocket)


@app.post("/api/workers/telemetry")
async def ingest_worker_telemetry(payload: WorkerTelemetryPayload) -> JSONResponse:
    WORKER_TELEMETRY[payload.worker_id] = payload
    return JSONResponse({"stored": True})


@app.get("/api/metrics")
async def metrics() -> JSONResponse:
    jobs_list = await job_manager.list_jobs()
    count_by_status: Dict[str, int] = {}
    for job in jobs_list:
        count_by_status[job.status] = count_by_status.get(job.status, 0) + 1
    worker_metrics = _worker_metrics_summary()
    return JSONResponse({"jobs": count_by_status, "workers": worker_metrics})


@app.get("/api/config")
async def get_config() -> JSONResponse:
    snapshot = config_service.snapshot
    libraries = {
        library.name: {
            "root": library.root,
            "depth": library.depth,
            "profile_id": library.profile_id,
        }
        for library in LIBRARY_CONFIG_STORE.list_libraries()
    }
    profiles = [profile.to_payload() for profile in PROFILE_STORE.list_profiles()]
    payload = config_module.sanitize_config(snapshot.config, revision=snapshot.revision)
    payload["libraries"] = libraries
    payload["profiles"] = profiles
    payload["environment"] = {"is_wsl2": HOST_ENVIRONMENT.get("is_wsl2", False)}
    return JSONResponse(payload, headers=_cache_headers(snapshot))


@app.post("/api/config/encoding")
async def update_encoding(payload: EncodingUpdatePayload) -> JSONResponse:
    try:
        profile_data, validated = _profile_data_from_payload(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc))
    profile = PROFILE_STORE.upsert(profile_data)
    snapshot = config_service.update_profile(payload.name, validated.model_dump())
    return JSONResponse(
        {"profile": profile.to_payload(), "revision": snapshot.revision},
        headers=_cache_headers(snapshot),
    )


@app.get("/api/profiles")
async def list_profiles() -> JSONResponse:
    profiles = [profile.to_payload() for profile in PROFILE_STORE.list_profiles()]
    return JSONResponse(profiles)


@app.get("/api/profiles/{profile_id}")
async def get_profile(profile_id: int) -> JSONResponse:
    profile = PROFILE_STORE.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return JSONResponse(profile.to_payload())


@app.post("/api/profiles")
async def create_profile(payload: EncodingUpdatePayload) -> JSONResponse:
    if PROFILE_STORE.get_by_name(payload.name):
        raise HTTPException(status_code=409, detail="Profile name already exists")
    try:
        profile_data, validated = _profile_data_from_payload(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc))
    profile = PROFILE_STORE.create(profile_data)
    snapshot = config_service.update_profile(payload.name, validated.model_dump())
    return JSONResponse(
        {"profile": profile.to_payload(), "revision": snapshot.revision},
        headers=_cache_headers(snapshot),
        status_code=201,
    )


@app.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: int, payload: EncodingUpdatePayload) -> JSONResponse:
    try:
        profile_data, validated = _profile_data_from_payload(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        profile = PROFILE_STORE.update(profile_id, profile_data)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    snapshot = config_service.update_profile(payload.name, validated.model_dump())
    return JSONResponse(
        {"profile": profile.to_payload(), "revision": snapshot.revision},
        headers=_cache_headers(snapshot),
    )


@app.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int) -> JSONResponse:
    try:
        PROFILE_STORE.delete(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse(status_code=204, content=None)


@app.get("/api/libraries")
async def list_libraries() -> JSONResponse:
    payload = []
    for library in LIBRARY_CONFIG_STORE.list_libraries():
        profile = PROFILE_STORE.get(library.profile_id)
        payload.append(
            {
                **library.to_payload(),
                "profile": profile.name if profile else None,
            }
        )
    return JSONResponse(payload)


@app.post("/api/libraries", status_code=201)
async def create_library(
    payload: LibraryCreatePayload, background_tasks: BackgroundTasks
) -> JSONResponse:
    normalized_name = payload.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Library name cannot be empty")
    if LIBRARY_CONFIG_STORE.get(normalized_name):
        raise HTTPException(status_code=409, detail="Library name already exists")
    profile = PROFILE_STORE.get(payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    root = payload.root.strip()
    if not root:
        raise HTTPException(status_code=400, detail="Library root is required")

    depth_value = "max"
    if payload.depth and payload.depth.lower() != "max":
        LOGGER.info(
            "Ignoring requested depth %s for library %s; full scans enforced.",
            payload.depth,
            normalized_name,
        )

    library = LIBRARY_CONFIG_STORE.upsert(
        LibraryData(
            name=normalized_name,
            root=root,
            depth=depth_value,
            profile_id=payload.profile_id,
        )
    )
    snapshot = config_service.upsert_library(
        normalized_name,
        root=library.root,
        depth=library.depth,
        profile=profile.name,
        profile_id=profile.id,
    )
    background_tasks.add_task(
        reconcile_library, library.name, library.root, profile.name, profile.id
    )
    payload = {**library.to_payload(), "profile": profile.name}
    await _emit_library_update("created", payload)
    return JSONResponse(payload, headers=_cache_headers(snapshot), status_code=201)


@app.patch("/api/libraries/{library_name}")
async def update_library_profile(library_name: str, payload: LibraryProfilePayload) -> JSONResponse:
    profile = PROFILE_STORE.get(payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    try:
        library = LIBRARY_CONFIG_STORE.update_profile(library_name, payload.profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Library not found")
    return JSONResponse({**library.to_payload(), "profile": profile.name})


@app.delete("/api/libraries/{library_name}")
async def delete_library(library_name: str) -> JSONResponse:
    library = LIBRARY_CONFIG_STORE.get(library_name)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    LIBRARY_CONFIG_STORE.delete(library_name)
    snapshot = config_service.delete_library(library_name)
    removed_entries = LIBRARY_STORE.mark_missing(library_name, set())
    await _emit_library_update("deleted", {"name": library_name, "entries_marked": removed_entries})
    return JSONResponse(
        {"deleted": library_name, "entries_marked": removed_entries},
        headers=_cache_headers(snapshot),
    )


@app.get("/api/logs")
async def list_logs(
    level: Optional[str] = None,
    min_severity: Optional[str] = None,
    query: Optional[str] = None,
    logger: Optional[str] = None,
    source: Optional[str] = None,
    category: Optional[str] = None,
) -> JSONResponse:
    severity_filter = None if min_severity == "ALL" else (min_severity or "INFO")
    entries = LOG_STORE.list_entries(
        level=level,
        min_severity=severity_filter,
        query=query,
        logger_name=logger,
        category=category,
        source=source,
        limit=200,
    )
    return JSONResponse(entries)


@app.get("/api/logs/categories")
async def list_log_categories() -> JSONResponse:
    return JSONResponse(LOG_STORE.list_categories())


@app.get("/api/logs/sources")
async def list_log_sources() -> JSONResponse:
    return JSONResponse(LOG_STORE.list_sources())


@app.get("/api/logs/stats")
async def log_stats() -> JSONResponse:
    return JSONResponse(LOG_STORE.stats())


@app.post("/api/logs/ingest")
async def ingest_logs(batch: LogIngestBatch) -> JSONResponse:
    stored = 0
    for entry in batch.entries:
        severity = entry.severity or entry.level
        source = entry.source
        category = entry.category
        if not source or not category:
            derived_source, derived_category = derive_source_category(entry.logger)
            source = source or derived_source
            category = category or derived_category
        LOG_STORE.add_entry(
            LogEntry(
                timestamp=entry.timestamp or datetime.now(timezone.utc),
                level=entry.level,
                severity=severity,
                severity_value=severity_value(severity),
                logger=entry.logger,
                source=source,
                category=category,
                message=entry.message,
            )
        )
        stored += 1
    return JSONResponse({"stored": stored})


@app.post("/api/config/logging")
async def update_logging(payload: LoggingUpdatePayload) -> JSONResponse:
    snapshot = config_service.update_logging(payload.retention_days)
    LOG_STORE.update_retention(snapshot.config.logging.retention_days)
    LOGGER.info("Updated log retention to %s days", payload.retention_days)
    return JSONResponse(
        {
            "retention_days": snapshot.config.logging.retention_days,
            "revision": snapshot.revision,
        },
        headers=_cache_headers(snapshot),
    )


@app.get("/api/jobs")
async def list_jobs() -> JSONResponse:
    jobs_list = await job_manager.list_jobs()
    return JSONResponse(jsonable_encoder([_job_to_response(job) for job in jobs_list]))


@app.post("/api/jobs/clear")
async def clear_completed_jobs() -> JSONResponse:
    removed = await job_manager.clear_processed()
    return JSONResponse({"removed": removed})


@app.get("/api/jobs/next")
async def next_job() -> JSONResponse:
    queue_state = await job_manager.queue_state()
    if queue_state["paused"]:
        return JSONResponse(queue_state | {"detail": "Queue paused"}, status_code=409)
    claimed = await job_manager.acquire_next("api")
    if claimed is None:
        raise HTTPException(status_code=204, detail="No jobs available")
    delivery_id, job = claimed
    _sync_entry_from_job(job, LibraryStatus.CONVERTING)
    _record_job_history(job, JobHistoryStatus.RUNNING)
    payload = _job_to_response(job)
    payload["delivery_id"] = delivery_id
    return JSONResponse(jsonable_encoder(payload))


@app.post("/api/jobs/{job_id}/status")
async def update_job_status(job_id: str, payload: JobStatusPayload) -> JSONResponse:
    try:
        job = await job_manager.update_job(job_id, jobs.JobStatusUpdate(**payload.model_dump()))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    completed = payload.status in {jobs.JobStatus.COMPLETED, jobs.JobStatus.FAILED}
    if payload.status == jobs.JobStatus.RUNNING:
        _sync_entry_from_job(job, LibraryStatus.CONVERTING, payload.message)
    elif payload.status == jobs.JobStatus.COMPLETED:
        _sync_entry_from_job(job, LibraryStatus.CONVERTED, payload.message)
    elif payload.status == jobs.JobStatus.FAILED:
        _sync_entry_from_job(job, LibraryStatus.FAILED, payload.message)
    _record_job_history(job, payload.status, payload.message, completed=completed)
    return JSONResponse(jsonable_encoder(_job_to_response(job)))


@app.post("/api/jobs/{job_id}/ack")
async def acknowledge_job(job_id: str, payload: JobAckPayload) -> JSONResponse:
    await job_manager.acknowledge(payload.delivery_id, job_id)
    return JSONResponse({"acknowledged": True})


@app.get("/api/queue/state")
async def queue_state() -> JSONResponse:
    state = await job_manager.queue_state()
    state["workers"] = _worker_metrics_summary()
    return JSONResponse(state)


@app.post("/api/queue/pause")
async def pause_queue(payload: QueuePauseRequest) -> JSONResponse:
    await job_manager.pause(payload.reason)
    LOGGER.warning("Job queue paused: %s", payload.reason or "no reason provided")
    return JSONResponse(await job_manager.queue_state())


@app.post("/api/queue/resume")
async def resume_queue() -> JSONResponse:
    await job_manager.resume()
    LOGGER.info("Job queue resumed")
    return JSONResponse(await job_manager.queue_state())


@app.get("/api/library/entries")
async def list_library_entries(
    status: Optional[str] = None,
    library: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    if status and status not in LIBRARY_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status filter")
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Limit must be greater than zero")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset cannot be negative")

    entries = LIBRARY_STORE.list_entries(status=status, library=library, limit=limit, offset=offset)
    return JSONResponse(jsonable_encoder([_entry_to_response(entry) for entry in entries]))


@app.patch("/api/library/entries/{entry_id}")
async def update_entry_profile(entry_id: int, payload: EntryProfilePayload) -> JSONResponse:
    entry = _get_entry_or_404(entry_id)
    profile = PROFILE_STORE.get(payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    updated = LIBRARY_STORE.update_status(
        entry.path,
        entry.status,
        library=entry.library,
        profile=profile.name,
        profile_id=payload.profile_id,
        job_id=entry.last_job_id,
        output_path=entry.output_path,
        original_missing=entry.original_missing,
    )
    return JSONResponse(jsonable_encoder(_entry_to_response(updated)))


@app.post("/api/library/entries/{entry_id}/reprocess")
async def reprocess_entry(
    entry_id: int, payload: Optional[ReprocessPayload] = None
) -> JSONResponse:
    entry = _get_entry_or_404(entry_id)
    source = Path(entry.path)
    if not source.exists():
        updated = LIBRARY_STORE.update_status(
            entry.path,
            LibraryStatus.REMOVED,
            message="Original missing",
            job_id=None,
            original_missing=True,
            profile=entry.profile,
            profile_id=entry.profile_id,
        )
        raise HTTPException(status_code=409, detail=_entry_to_response(updated))
    profile_id = payload.profile_id if payload else None
    profile_name = entry.profile
    if profile_id is None:
        profile_id = entry.profile_id
    if profile_id is None:
        _, profile = _library_profile(entry.library)
        profile_id = profile.id
        profile_name = profile.name
    else:
        profile = PROFILE_STORE.get(profile_id)
        if profile:
            profile_name = profile.name
    if profile_id is None:
        raise HTTPException(status_code=404, detail="Profile not available for entry")
    job = await job_manager.add_job(
        entry.path,
        entry.library,
        profile_name,
        profile_id=profile_id,
        encoding=encoding_payload(profile_id),
        force=True,
    )
    updated = LIBRARY_STORE.update_status(
        entry.path,
        LibraryStatus.PENDING,
        job_id=job.id,
        output_path=str(job_manager.output_path(source)),
        original_missing=False,
        profile=profile_name,
        profile_id=profile_id,
    )
    payload = {"entry": _entry_to_response(updated), "job": job.model_dump()}
    return JSONResponse(jsonable_encoder(payload))


@app.post("/api/library/entries/{entry_id}/remove-original")
async def remove_original(entry_id: int) -> JSONResponse:
    entry = _get_entry_or_404(entry_id)
    source = _resolve_media_path(entry.path)
    output_path = Path(entry.output_path or job_manager.output_path(source))
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=409, detail="Converted output missing or empty")

    if not source.exists():
        updated = LIBRARY_STORE.update_status(
            entry.path,
            LibraryStatus.REMOVED,
            original_missing=True,
            output_path=str(output_path),
            profile=entry.profile,
            profile_id=entry.profile_id,
        )
        return JSONResponse(jsonable_encoder({"entry": _entry_to_response(updated)}))

    try:
        source.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    updated = LIBRARY_STORE.update_status(
        entry.path,
        LibraryStatus.REMOVED,
        job_id=entry.last_job_id,
        output_path=str(output_path),
        original_missing=True,
        profile=entry.profile,
        profile_id=entry.profile_id,
    )
    return JSONResponse(jsonable_encoder({"entry": _entry_to_response(updated)}))


@app.post("/api/scan")
async def manual_scan(payload: ScanRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    libraries = _library_map()
    if payload.library:
        if payload.library not in libraries:
            raise HTTPException(status_code=404, detail="Library not found")
        target_libs = {payload.library: libraries[payload.library]}
    else:
        target_libs = libraries

    scheduled: List[str] = []
    for name, library in target_libs.items():
        root_path = payload.root or library.root
        profile = PROFILE_STORE.get(library.profile_id)
        if profile is None:
            LOGGER.warning(
                "Skipping manual scan for %s; profile id %s missing", name, library.profile_id
            )
            continue
        background_tasks.add_task(
            reconcile_library,
            name,
            root_path,
            profile.name,
            profile.id,
        )
        scheduled.append(name)
    return JSONResponse({"scheduled": scheduled})


@app.post("/api/events")
async def handle_event(payload: EventBatch | EventPayload) -> JSONResponse:
    events = payload.events if isinstance(payload, EventBatch) else [payload]
    processed: List[Dict[str, Any]] = []

    for event in events:
        result = await _process_event_payload(event)
        if result:
            processed.append(result)

    return JSONResponse(jsonable_encoder({"processed": processed, "count": len(processed)}))
