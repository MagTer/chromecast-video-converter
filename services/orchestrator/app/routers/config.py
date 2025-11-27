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
from ..profiles import ProfileData
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
