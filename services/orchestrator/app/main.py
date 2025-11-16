from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import config as config_module
from . import jellyfin, jobs

LOGGER = logging.getLogger("orchestrator")
logging.basicConfig(level=logging.INFO)

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/quality.sample.yaml")).resolve()
config_source = config_module.load_config(CONFIG_PATH)
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


def sanitize_config(config: config_module.QualityConfig) -> Dict[str, Any]:
    data = config.dict()
    jellyfin_cfg = data.get("jellyfin")
    if jellyfin_cfg:
        jellyfin_cfg["api_key"] = "REDACTED"
    return data


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
        await job_manager.scan_directory(name, library.root, library.profile)

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


@app.get("/api/jobs")
async def list_jobs() -> JSONResponse:
    jobs_list = await job_manager.list_jobs()
    return JSONResponse(jsonable_encoder([job.model_dump() for job in jobs_list]))


@app.get("/api/jobs/next")
async def next_job() -> JSONResponse:
    job = await job_manager.acquire_next()
    if job is None:
        raise HTTPException(status_code=204, detail="No jobs available")
    return JSONResponse(jsonable_encoder(job.model_dump()))


@app.post("/api/jobs/{job_id}/status")
async def update_job_status(job_id: str, payload: JobStatusPayload) -> JSONResponse:
    try:
        job = await job_manager.update_job(job_id, jobs.JobStatusUpdate(**payload.dict()))
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(jsonable_encoder(job.dict()))


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
        background_tasks.add_task(job_manager.scan_directory, name, root_path, library.profile)
        scheduled.append(name)
    return JSONResponse({"scheduled": scheduled})


@app.post("/api/events")
async def handle_event(payload: EventPayload) -> JSONResponse:
    library_name = payload.library or find_library_for_path(payload.path)
    if not library_name:
        raise HTTPException(status_code=400, detail="Library could not be determined")
    profile = config_source.config.libraries[library_name].profile
    job = await job_manager.add_job(payload.path, library_name, profile)
    return JSONResponse(jsonable_encoder(job.dict()))
