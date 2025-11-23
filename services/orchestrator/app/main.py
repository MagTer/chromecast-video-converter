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
from .library_entries import EntryUpdate, LibraryEntry, LibraryEntryStore, LibraryStatus
from .logs import LogEntry, LogStore, SQLiteLogHandler

logging.addLevelName(logging.DEBUG, "VERBOSE")

LOG_DB_PATH = Path(os.environ.get("LOG_DB_PATH", "/app/logs/events.db")).resolve()
LOG_STORE = LogStore(LOG_DB_PATH)
LIBRARY_DB_PATH = Path(os.environ.get("LIBRARY_DB_PATH", "/app/logs/library.db")).resolve()
LIBRARY_STORE = LibraryEntryStore(LIBRARY_DB_PATH)
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

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/settings.yaml")).resolve()
config_source = config_module.load_config(CONFIG_PATH)
LOG_STORE.update_retention(config_source.config.logging.retention_days)
job_manager = jobs.JobManager()

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


class JobStatusPayload(BaseModel):
    status: str
    progress: Optional[int] = None
    message: Optional[str] = None


class QueuePauseRequest(BaseModel):
    reason: Optional[str] = None


class LibraryEntryResponse(BaseModel):
    id: int
    path: str
    library: str
    profile: str
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


def sanitize_config(config: config_module.QualityConfig) -> Dict[str, Any]:
    data = config.model_dump()
    jellyfin_cfg = data.get("jellyfin")
    if jellyfin_cfg:
        jellyfin_cfg["api_key"] = "REDACTED"
    return data


def encoding_payload(profile_name: str) -> Dict[str, Any]:
    profile = config_source.config.profile_named(profile_name)
    return profile.model_dump()


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


def _entry_status_for_path(path: Path) -> tuple[str, Path, bool]:
    output_path = job_manager.output_path(path)
    if not path.exists():
        return LibraryStatus.REMOVED, output_path, False
    if job_manager.is_converted(path):
        return LibraryStatus.CONVERTED, output_path, True
    return LibraryStatus.PENDING, output_path, True


async def _record_library_entry(
    library_name: str, path: Path, profile: str
) -> tuple[LibraryEntry, Optional[jobs.Job]]:
    status, output_path, original_exists = _entry_status_for_path(path)
    entry = LIBRARY_STORE.upsert(
        EntryUpdate(
            path=str(path),
            library=library_name,
            profile=profile,
            status=status,
            output_path=str(output_path),
            original_missing=not original_exists,
        ),
    )
    if status == LibraryStatus.PENDING:
        job = await job_manager.add_job(
            str(path), library_name, profile, encoding=encoding_payload(profile)
        )
        LIBRARY_STORE.attach_job(entry.id, job.id)
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
        message=message,
        job_id=job.id,
        output_path=str(job_manager.output_path(source)),
        original_missing=original_missing,
    )


def _get_entry_or_404(entry_id: int) -> LibraryEntry:
    entry = LIBRARY_STORE.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Library entry not found")
    return entry


async def reconcile_library(library_name: str, root: str, profile: str) -> None:
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
        await _record_library_entry(library_name, entry, profile)
    removed = LIBRARY_STORE.mark_missing(library_name, seen)
    if removed:
        LOGGER.info("Marked %s missing entries as removed for library %s", removed, library_name)


def find_library_for_path(path: str) -> Optional[str]:
    try:
        normalized = Path(path).resolve()
    except FileNotFoundError:
        normalized = Path(path)
    for name, library in config_source.config.libraries.items():
        library_root = Path(library.root)
        for candidate_root in _candidate_library_roots(library_root):
            if normalized.is_relative_to(candidate_root):
                return name
    return None


@app.on_event("startup")
async def startup_event() -> None:
    LOGGER.info("Starting initial scan of configured libraries.")
    for name, library in config_source.config.libraries.items():
        LOGGER.info("Scanning library %s at %s", name, library.root)
        await reconcile_library(name, library.root, library.profile)

    jellyfin_cfg = config_source.config.jellyfin
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
    return JSONResponse({"status": "ok", "libraries": len(config_source.config.libraries)})


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
    return JSONResponse(sanitize_config(config_source.config))


@app.post("/api/config/encoding")
async def update_encoding(payload: EncodingUpdatePayload) -> JSONResponse:
    try:
        profile = config_module.update_profile(
            config_source,
            payload.name,
            {
                "codec": payload.codec,
                "profile": payload.profile,
                "level": payload.level,
                "resolution": payload.resolution,
                "max_fps": payload.max_fps,
                "max_bitrate": payload.max_bitrate,
                "bufsize": payload.bufsize,
                "preset": payload.preset,
                "cq": payload.cq,
                "rc": payload.rc,
                "audio": payload.audio.model_dump(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse({"name": payload.name, "profile": profile.model_dump()})


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
    config_source.config.logging.retention_days = payload.retention_days
    config_module.persist_config(config_source)
    LOG_STORE.update_retention(payload.retention_days)
    LOGGER.info("Updated log retention to %s days", payload.retention_days)
    return JSONResponse({"retention_days": payload.retention_days})


@app.get("/api/jobs")
async def list_jobs() -> JSONResponse:
    jobs_list = await job_manager.list_jobs()
    return JSONResponse(jsonable_encoder([job.model_dump() for job in jobs_list]))


@app.get("/api/jobs/next")
async def next_job() -> JSONResponse:
    queue_state = await job_manager.queue_state()
    if queue_state["paused"]:
        return JSONResponse(queue_state | {"detail": "Queue paused"}, status_code=409)
    job = await job_manager.acquire_next()
    if job is None:
        raise HTTPException(status_code=204, detail="No jobs available")
    _sync_entry_from_job(job, LibraryStatus.CONVERTING)
    return JSONResponse(jsonable_encoder(job.model_dump()))


@app.post("/api/jobs/{job_id}/status")
async def update_job_status(job_id: str, payload: JobStatusPayload) -> JSONResponse:
    try:
        job = await job_manager.update_job(job_id, jobs.JobStatusUpdate(**payload.model_dump()))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    if payload.status == jobs.JobStatus.RUNNING:
        _sync_entry_from_job(job, LibraryStatus.CONVERTING, payload.message)
    elif payload.status == jobs.JobStatus.COMPLETED:
        _sync_entry_from_job(job, LibraryStatus.CONVERTED, payload.message)
    elif payload.status == jobs.JobStatus.FAILED:
        _sync_entry_from_job(job, LibraryStatus.FAILED, payload.message)
    return JSONResponse(jsonable_encoder(job.model_dump()))


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


@app.post("/api/library/entries/{entry_id}/reprocess")
async def reprocess_entry(entry_id: int) -> JSONResponse:
    entry = _get_entry_or_404(entry_id)
    source = Path(entry.path)
    if not source.exists():
        updated = LIBRARY_STORE.update_status(
            entry.path,
            LibraryStatus.REMOVED,
            message="Original missing",
            job_id=None,
            original_missing=True,
        )
        raise HTTPException(status_code=409, detail=_entry_to_response(updated))
    job = await job_manager.add_job(
        entry.path,
        entry.library,
        entry.profile,
        encoding=encoding_payload(entry.profile),
        force=True,
    )
    updated = LIBRARY_STORE.update_status(
        entry.path,
        LibraryStatus.PENDING,
        job_id=job.id,
        output_path=str(job_manager.output_path(source)),
        original_missing=False,
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
    )
    return JSONResponse(jsonable_encoder({"entry": _entry_to_response(updated)}))


@app.post("/api/scan")
async def manual_scan(payload: ScanRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    if payload.library:
        if payload.library not in config_source.config.libraries:
            raise HTTPException(status_code=404, detail="Library not found")
        target_libs = {payload.library: config_source.config.libraries[payload.library]}
    else:
        target_libs = config_source.config.libraries

    scheduled: List[str] = []
    for name, library in target_libs.items():
        root_path = payload.root or library.root
        background_tasks.add_task(
            reconcile_library,
            name,
            root_path,
            library.profile,
        )
        scheduled.append(name)
    return JSONResponse({"scheduled": scheduled})


@app.post("/api/events")
async def handle_event(payload: EventPayload) -> JSONResponse:
    library_name = payload.library or find_library_for_path(payload.path)
    if not library_name:
        raise HTTPException(status_code=400, detail="Library could not be determined")
    profile = config_source.config.libraries[library_name].profile
    try:
        entry, job = await _record_library_entry(library_name, Path(payload.path), profile)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    response: Dict[str, Any] = {"entry": LibraryEntryResponse.model_validate(entry)}
    if job:
        response["job"] = job.model_dump()
    return JSONResponse(jsonable_encoder(response))
