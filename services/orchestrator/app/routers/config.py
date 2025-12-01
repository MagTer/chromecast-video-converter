import logging
from typing import Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .. import config as config_module
from ..dependencies import (
    HOST_ENVIRONMENT,
    LIBRARY_CONFIG_STORE,
    LOG_STORE,
    PROFILE_STORE,
    config_service,
)
from ..profiles import HardwareProfileData, ProfileData
from ..schemas import EncodingUpdatePayload, LoggingUpdatePayload

LOGGER = logging.getLogger("orchestrator.config")
router = APIRouter()


def cache_headers(snapshot: config_module.ConfigSnapshot) -> Dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Config-Revision": str(snapshot.revision),
    }


def profile_data_from_payload(
    payload: EncodingUpdatePayload,
) -> tuple[ProfileData, config_module.ProfileSet]:
    gpu_raw = payload.gpu.model_dump()
    gpu_raw["mode"] = "gpu"
    cpu_raw = payload.cpu.model_dump()
    cpu_raw["mode"] = "cpu"
    gpu_validated = config_module.HardwareProfile(**gpu_raw)
    cpu_validated = config_module.HardwareProfile(**cpu_raw)
    profile_set = config_module.ProfileSet(gpu=gpu_validated, cpu=cpu_validated)
    profile_data = ProfileData(
        name=payload.name,
        gpu=HardwareProfileData(
            mode="gpu",
            codec=gpu_validated.codec,
            profile=gpu_validated.profile,
            level=gpu_validated.level,
            resolution=gpu_validated.resolution,
            max_fps=gpu_validated.max_fps,
            bitrate=gpu_validated.bitrate,
            max_bitrate=gpu_validated.max_bitrate,
            bufsize=gpu_validated.bufsize,
            preset=gpu_validated.preset,
            cq=gpu_validated.cq,
            rc=gpu_validated.rc,
            bframes=gpu_validated.bframes,
            lookahead=gpu_validated.lookahead,
            adaptive_b_frames=gpu_validated.adaptive_b_frames,
            aq=gpu_validated.aq,
            spatial_aq=gpu_validated.spatial_aq,
            temporal_aq=gpu_validated.temporal_aq,
            aq_strength=getattr(gpu_validated, "aq_strength", 7),
            audio_codec=gpu_validated.audio.codec,
            audio_bitrate=gpu_validated.audio.bitrate,
            audio_channels=gpu_validated.audio.channels,
        ),
        cpu=HardwareProfileData(
            mode="cpu",
            codec=cpu_validated.codec,
            profile=cpu_validated.profile,
            level=cpu_validated.level,
            resolution=cpu_validated.resolution,
            max_fps=cpu_validated.max_fps,
            bitrate=cpu_validated.bitrate,
            max_bitrate=cpu_validated.max_bitrate,
            bufsize=cpu_validated.bufsize,
            preset=cpu_validated.preset,
            cq=cpu_validated.cq,
            rc=cpu_validated.rc,
            bframes=cpu_validated.bframes,
            lookahead=cpu_validated.lookahead,
            adaptive_b_frames=cpu_validated.adaptive_b_frames,
            aq=cpu_validated.aq,
            spatial_aq=cpu_validated.spatial_aq,
            temporal_aq=cpu_validated.temporal_aq,
            aq_strength=getattr(cpu_validated, "aq_strength", 7),
            audio_codec=cpu_validated.audio.codec,
            audio_bitrate=cpu_validated.audio.bitrate,
            audio_channels=cpu_validated.audio.channels,
        ),
    )
    return profile_data, profile_set


@router.get("/api/config")
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
    return JSONResponse(payload, headers=cache_headers(snapshot))


@router.post("/api/config/encoding")
async def update_encoding(payload: EncodingUpdatePayload) -> JSONResponse:
    try:
        profile_data, validated = profile_data_from_payload(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc))
    profile = PROFILE_STORE.upsert(profile_data)
    snapshot = config_service.update_profile(payload.name, validated.model_dump())
    return JSONResponse(
        {"profile": profile.to_payload(), "revision": snapshot.revision},
        headers=cache_headers(snapshot),
    )


@router.get("/api/profiles")
async def list_profiles() -> JSONResponse:
    profiles = [profile.to_payload() for profile in PROFILE_STORE.list_profiles()]
    return JSONResponse(profiles)


@router.get("/api/profiles/{profile_id}")
async def get_profile(profile_id: int) -> JSONResponse:
    profile = PROFILE_STORE.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return JSONResponse(profile.to_payload())


@router.post("/api/profiles")
async def create_profile(payload: EncodingUpdatePayload) -> JSONResponse:
    if PROFILE_STORE.get_by_name(payload.name):
        raise HTTPException(status_code=409, detail="Profile name already exists")
    try:
        profile_data, validated = profile_data_from_payload(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc))
    profile = PROFILE_STORE.create(profile_data)
    snapshot = config_service.update_profile(payload.name, validated.model_dump())
    return JSONResponse(
        {"profile": profile.to_payload(), "revision": snapshot.revision},
        headers=cache_headers(snapshot),
        status_code=201,
    )


@router.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: int, payload: EncodingUpdatePayload) -> JSONResponse:
    try:
        profile_data, validated = profile_data_from_payload(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        profile = PROFILE_STORE.update(profile_id, profile_data)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    snapshot = config_service.update_profile(payload.name, validated.model_dump())
    return JSONResponse(
        {"profile": profile.to_payload(), "revision": snapshot.revision},
        headers=cache_headers(snapshot),
    )


@router.delete("/api/profiles/{profile_id}")
async def delete_profile(profile_id: int) -> JSONResponse:
    try:
        PROFILE_STORE.delete(profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return JSONResponse(status_code=204, content=None)


@router.post("/api/config/logging")
async def update_logging(payload: LoggingUpdatePayload) -> JSONResponse:
    snapshot = config_service.update_logging(payload.retention_days)
    LOG_STORE.update_retention(snapshot.config.logging.retention_days)
    LOGGER.info("Updated log retention to %s days", payload.retention_days)
    return JSONResponse(
        {
            "retention_days": snapshot.config.logging.retention_days,
            "revision": snapshot.revision,
        },
        headers=cache_headers(snapshot),
    )


@router.post("/api/config/reset")
async def reset_config() -> JSONResponse:
    """Reset configuration to built-in defaults."""
    snapshot = config_service.reset_to_defaults()
    # Note: We do not reset the PROFILE_STORE here to avoid breaking existing IDs
    # referenced by jobs. The user can manually clean up profiles if needed.

    # We also need to update LOG_STORE retention
    LOG_STORE.update_retention(snapshot.config.logging.retention_days)

    return JSONResponse(
        config_module.sanitize_config(snapshot.config, revision=snapshot.revision),
        headers=cache_headers(snapshot),
    )
