from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import config as config_module
from . import jellyfin, jobs
from .logs import LogEntry, LogStore, SQLiteLogHandler

LOG_DB_PATH = Path(os.environ.get("LOG_DB_PATH", "/app/logs/events.db")).resolve()
LOG_STORE = LogStore(LOG_DB_PATH)


def configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    sqlite_handler = SQLiteLogHandler(LOG_STORE)
    sqlite_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
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


def find_library_for_path(path: str) -> Optional[str]:
    try:
        normalized = Path(path).resolve()
    except FileNotFoundError:
        normalized = Path(path)
    for name, library in config_source.config.libraries.items():
        try:
            library_root = Path(library.root).resolve()
        except FileNotFoundError:
            continue
        if normalized.is_relative_to(library_root):
            return name
    return None


@app.on_event("startup")
async def startup_event() -> None:
    LOGGER.info("Starting initial scan of configured libraries.")
    for name, library in config_source.config.libraries.items():
        LOGGER.info("Scanning library %s at %s", name, library.root)
        await job_manager.scan_directory(
            name, library.root, library.profile, encoding=encoding_payload(library.profile)
        )

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
                timestamp=entry.timestamp or datetime.utcnow(),
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
    return JSONResponse(jsonable_encoder(job.model_dump()))


@app.post("/api/jobs/{job_id}/status")
async def update_job_status(job_id: str, payload: JobStatusPayload) -> JSONResponse:
    try:
        job = await job_manager.update_job(job_id, jobs.JobStatusUpdate(**payload.model_dump()))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
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
            job_manager.scan_directory,
            name,
            root_path,
            library.profile,
            encoding_payload(library.profile),
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
        job = await job_manager.add_job(
            payload.path, library_name, profile, encoding=encoding_payload(profile)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse(jsonable_encoder(job.model_dump()))
