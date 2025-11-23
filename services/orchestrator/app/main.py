from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import config as config_module
from . import jellyfin, jobs
from .db import Base, create_session_factory
from .job_history import JobHistoryEntry, JobHistoryStatus, JobHistoryStore
from .library_entries import EntryUpdate, LibraryEntry, LibraryEntryStore, LibraryStatus
from .logs import LogEntry, LogStore, SQLiteLogHandler
from .profiles import (
    EncodingProfile,
    LibraryConfig,
    LibraryConfigStore,
    LibraryData,
    ProfileData,
    ProfileStore,
)

logging.addLevelName(logging.DEBUG, "VERBOSE")

LOG_DB_PATH = Path(os.environ.get("LOG_DB_PATH", "/app/logs/events.db")).resolve()
CONFIG_DB_PATH = Path(os.environ.get("CONFIG_DB_PATH", "/app/logs/config.db")).resolve()
CONFIG_TEMPLATE_PATH = Path(
    os.environ.get("CONFIG_TEMPLATE_PATH", "/app/config/settings.yaml.template")
).resolve()
LEGACY_CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/settings.yaml")).resolve()

LOG_STORE = LogStore(LOG_DB_PATH)
LIBRARY_DB_PATH = Path(os.environ.get("LIBRARY_DB_PATH", "/app/logs/library.db")).resolve()
SESSION_FACTORY, ENGINE = create_session_factory(LIBRARY_DB_PATH)
Base.metadata.create_all(ENGINE)
PROFILE_STORE = ProfileStore(SESSION_FACTORY)
LIBRARY_CONFIG_STORE = LibraryConfigStore(SESSION_FACTORY)
LIBRARY_STORE = LibraryEntryStore(LIBRARY_DB_PATH, session_factory=SESSION_FACTORY, engine=ENGINE)
JOB_HISTORY_STORE = JobHistoryStore(SESSION_FACTORY)
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


def configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    sqlite_handler = SQLiteLogHandler(LOG_STORE)
    sqlite_handler.setLevel(logging.DEBUG)
    sqlite_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(sqlite_handler)


configure_logging()

LOGGER = logging.getLogger("orchestrator")

TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"
INDEX_HTML = TEMPLATE_PATH.read_text()

app = FastAPI(title="Chromecast Transcode Orchestrator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


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
    max_fps: int = Field(default=30, gt=0, le=30)
    max_bitrate: str
    bufsize: str
    preset: str
    cq: int = Field(ge=0, le=30)
    rc: str
    audio: config_module.AudioProfile


class LoggingUpdatePayload(BaseModel):
    retention_days: int = Field(ge=1, le=90)


class LogIngestEvent(BaseModel):
    logger: str
    level: str
    message: str
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
                max_bitrate=profile.max_bitrate,
                bufsize=profile.bufsize,
                preset=profile.preset,
                cq=profile.cq,
                rc=profile.rc,
                level=profile.level,
                max_fps=profile.max_fps,
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
        max_bitrate=payload.max_bitrate,
        bufsize=payload.bufsize,
        preset=payload.preset,
        cq=payload.cq,
        rc=payload.rc,
        audio=payload.audio,
    )
    profile_data = ProfileData(
        name=payload.name,
        codec=validated.codec,
        profile_tier=validated.profile,
        max_resolution=validated.resolution,
        max_bitrate=validated.max_bitrate,
        bufsize=validated.bufsize,
        preset=validated.preset,
        cq=validated.cq,
        rc=validated.rc,
        level=validated.level,
        max_fps=validated.max_fps,
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
        "max_bitrate": profile.max_bitrate,
        "bufsize": profile.bufsize,
        "preset": profile.preset,
        "cq": profile.cq,
        "rc": profile.rc,
        "level": profile.level,
        "max_fps": profile.max_fps,
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
    if job_manager.is_converted(path):
        return LibraryStatus.CONVERTED, output_path, True
    return LibraryStatus.PENDING, output_path, True


async def _record_library_entry(
    library_name: str, path: Path, profile: str, profile_id: int
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
        )
        LIBRARY_STORE.attach_job(entry.id, job.id)
        _record_job_history(job, JobHistoryStatus.PENDING)
        return entry, job
    return entry, None


def _entry_to_response(entry: LibraryEntry) -> Dict[str, Any]:
    return LibraryEntryResponse.model_validate(entry).model_dump()


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
    for entry in root_path.rglob("*.*"):
        if not _should_track_file(entry):
            continue
        seen.add(str(entry))
        await _record_library_entry(library_name, entry, profile, profile_id)
    removed = LIBRARY_STORE.mark_missing(library_name, seen)
    if removed:
        LOGGER.info("Marked %s missing entries as removed for library %s", removed, library_name)


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


@app.get("/api/metrics")
async def metrics() -> JSONResponse:
    jobs_list = await job_manager.list_jobs()
    count_by_status: Dict[str, int] = {}
    for job in jobs_list:
        count_by_status[job.status] = count_by_status.get(job.status, 0) + 1
    return JSONResponse({"jobs": count_by_status})


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
    in_use = [
        library.name
        for library in LIBRARY_CONFIG_STORE.list_libraries()
        if library.profile_id == profile_id
    ]
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Profile in use by libraries: {', '.join(in_use)}",
        )
    try:
        PROFILE_STORE.delete(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
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


@app.get("/api/logs")
async def list_logs(
    level: Optional[str] = None,
    query: Optional[str] = None,
    logger: Optional[str] = None,
) -> JSONResponse:
    entries = LOG_STORE.list_entries(level=level, query=query, logger_name=logger, limit=200)
    return JSONResponse(entries)


@app.get("/api/logs/categories")
async def list_log_categories() -> JSONResponse:
    return JSONResponse(LOG_STORE.list_categories())


@app.get("/api/logs/stats")
async def log_stats() -> JSONResponse:
    return JSONResponse(LOG_STORE.stats())


@app.post("/api/logs/ingest")
async def ingest_logs(batch: LogIngestBatch) -> JSONResponse:
    stored = 0
    for entry in batch.entries:
        LOG_STORE.add_entry(
            LogEntry(
                timestamp=entry.timestamp or datetime.now(timezone.utc),
                level=entry.level,
                logger=entry.logger,
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
    return JSONResponse(jsonable_encoder([job.model_dump() for job in jobs_list]))


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
    return JSONResponse(jsonable_encoder(job.model_dump() | {"delivery_id": delivery_id}))


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
    return JSONResponse(jsonable_encoder(job.model_dump()))


@app.post("/api/jobs/{job_id}/ack")
async def acknowledge_job(job_id: str, payload: JobAckPayload) -> JSONResponse:
    await job_manager.acknowledge(payload.delivery_id, job_id)
    return JSONResponse({"acknowledged": True})


@app.get("/api/queue/state")
async def queue_state() -> JSONResponse:
    return JSONResponse(await job_manager.queue_state())


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
    status: Optional[str] = None, library: Optional[str] = None
) -> JSONResponse:
    if status and status not in LIBRARY_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status filter")
    entries = LIBRARY_STORE.list_entries(status=status, library=library)
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
    output_path = Path(entry.output_path or job_manager.output_path(Path(entry.path)))
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=409, detail="Converted output missing or empty")

    source = Path(entry.path)
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
