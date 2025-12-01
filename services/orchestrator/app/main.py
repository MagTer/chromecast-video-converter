from __future__ import annotations

import asyncio
import logging
from typing import Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config as config_module
from . import (
    jellyfin,
    jobs,  # noqa: F401
)
from .dependencies import (
    JOB_HISTORY_STORE,  # noqa: F401
    LIBRARY_CONFIG_STORE,
    LIBRARY_STORE,  # noqa: F401
    LOG_STORE,
    PROFILE_STORE,
    STATIC_DIR,
    config_service,
    job_manager,
)
from .logs import SQLiteLogHandler, StructuredLogFilter
from .profiles import HardwareProfileData, LibraryData, ProfileData
from .routers import (
    config as config_router,
)
from .routers import (
    history as history_router,
)
from .routers import (
    jobs as jobs_router,
)
from .routers import (
    libraries as libraries_router,
)
from .routers import (
    logs as logs_router,
)
from .routers import (
    system as system_router,
)
from .services.core import encoding_payload, reconcile_library  # noqa: F401


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


def seed_profiles_and_libraries(snapshot: config_module.ConfigSnapshot) -> None:
    name_to_id: Dict[str, int] = {}
    for name, profile in snapshot.config.profiles.items():
        gpu = profile.gpu
        cpu = profile.cpu
        record = PROFILE_STORE.upsert(
            ProfileData(
                name=name,
                gpu=HardwareProfileData(
                    mode="gpu",
                    codec=gpu.codec,
                    profile=gpu.profile,
                    level=gpu.level,
                    resolution=gpu.resolution,
                    max_fps=gpu.max_fps,
                    bitrate=gpu.bitrate,
                    max_bitrate=gpu.max_bitrate,
                    bufsize=gpu.bufsize,
                    preset=gpu.preset,
                    cq=gpu.cq,
                    rc=gpu.rc,
                    bframes=gpu.bframes,
                    lookahead=gpu.lookahead,
                    adaptive_b_frames=gpu.adaptive_b_frames,
                    aq=gpu.aq,
                    spatial_aq=gpu.spatial_aq,
                    temporal_aq=gpu.temporal_aq,
                    aq_strength=getattr(gpu, "aq_strength", 7),
                    audio_codec=gpu.audio.codec,
                    audio_bitrate=gpu.audio.bitrate,
                    audio_channels=gpu.audio.channels,
                ),
                cpu=HardwareProfileData(
                    mode="cpu",
                    codec=cpu.codec,
                    profile=cpu.profile,
                    level=cpu.level,
                    resolution=cpu.resolution,
                    max_fps=cpu.max_fps,
                    bitrate=cpu.bitrate,
                    max_bitrate=cpu.max_bitrate,
                    bufsize=cpu.bufsize,
                    preset=cpu.preset,
                    cq=cpu.cq,
                    rc=cpu.rc,
                    bframes=cpu.bframes,
                    lookahead=cpu.lookahead,
                    adaptive_b_frames=cpu.adaptive_b_frames,
                    aq=cpu.aq,
                    spatial_aq=cpu.spatial_aq,
                    temporal_aq=cpu.temporal_aq,
                    aq_strength=getattr(cpu, "aq_strength", 7),
                    audio_codec=cpu.audio.codec,
                    audio_bitrate=cpu.audio.bitrate,
                    audio_channels=cpu.audio.channels,
                ),
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


# Initialize Config
config_snapshot = config_service.reload()
seed_profiles_and_libraries(config_snapshot)
LOG_STORE.update_retention(config_snapshot.config.logging.retention_days)

app = FastAPI(title="Chromecast Transcode Orchestrator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(system_router.router)
app.include_router(jobs_router.router)
app.include_router(history_router.router)
app.include_router(libraries_router.router)
app.include_router(config_router.router)
app.include_router(logs_router.router)


async def _safe_jellyfin_trigger(jellyfin_cfg: config_module.JellyfinConfig) -> None:
    try:
        await jellyfin.trigger_all(jellyfin_cfg.libraries, jellyfin_cfg.url, jellyfin_cfg.api_key)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Jellyfin refresh failed", exc_info=exc)


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
