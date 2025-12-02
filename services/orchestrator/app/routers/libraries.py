import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .. import config as config_module
from ..dependencies import (
    NOTIFIER,
    get_app_dependencies,
    get_library_map,
)
from ..profiles import LibraryData
from ..schemas import (
    EntryProfilePayload,
    EventBatch,
    EventPayload,
    LibraryCreatePayload,
    LibraryProfilePayload,
    ReprocessPayload,
    ScanRequest,
)
from ..services.core import (
    LibraryStatus,
    encoding_payload,
    entry_to_response,
    get_library_profile,
    job_to_response,
    process_event_payload,
    reconcile_library,
)
from ..utils import resolve_media_path

LOGGER = logging.getLogger("orchestrator.libraries")
router = APIRouter()


async def emit_library_update(action: str, payload: Dict[str, Any]) -> None:
    await NOTIFIER.broadcast({"type": "library-update", "action": action, "library": payload})


def cache_headers(snapshot: config_module.ConfigSnapshot) -> Dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Config-Revision": str(snapshot.revision),
    }


@router.get("/api/libraries")
async def list_libraries() -> JSONResponse:
    payload = []
    for library in get_app_dependencies().library_config_store.list_libraries():
        profile = get_app_dependencies().profile_store.get(library.profile_id)
        payload.append(
            {
                **library.to_payload(),
                "profile": profile.name if profile else None,
            }
        )
    return JSONResponse(payload)


@router.post("/api/libraries", status_code=201)
async def create_library(
    payload: LibraryCreatePayload, background_tasks: BackgroundTasks
) -> JSONResponse:
    normalized_name = payload.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Library name cannot be empty")
    if get_app_dependencies().library_config_store.get(normalized_name):
        raise HTTPException(status_code=409, detail="Library name already exists")
    profile = get_app_dependencies().profile_store.get(payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    root = payload.root.strip()
    if not root:
        raise HTTPException(status_code=400, detail="Library root is required")
    canonical_root = str(resolve_media_path(root))

    depth_value = "max"
    if payload.depth and payload.depth.lower() != "max":
        LOGGER.info(
            "Ignoring requested depth %s for library %s; full scans enforced.",
            payload.depth,
            normalized_name,
        )

    library = get_app_dependencies().library_config_store.upsert(
        LibraryData(
            name=normalized_name,
            root=canonical_root,
            depth=depth_value,
            profile_id=payload.profile_id,
        )
    )
    snapshot = get_app_dependencies().config_service.upsert_library(
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
    await emit_library_update("created", payload)
    return JSONResponse(payload, headers=cache_headers(snapshot), status_code=201)


@router.patch("/api/libraries/{library_name}")
async def update_library_profile(library_name: str, payload: LibraryProfilePayload) -> JSONResponse:
    profile = get_app_dependencies().profile_store.get(payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    try:
        library = get_app_dependencies().library_config_store.update_profile(library_name, payload.profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Library not found")
    return JSONResponse({**library.to_payload(), "profile": profile.name})


@router.delete("/api/libraries/{library_name}")
async def delete_library(library_name: str) -> JSONResponse:
    library = get_app_dependencies().library_config_store.get(library_name)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    get_app_dependencies().library_config_store.delete(library_name)
    snapshot = get_app_dependencies().config_service.delete_library(library_name)
    removed_entries = get_app_dependencies().library_entry_store.mark_missing(library_name, set())
    await emit_library_update("deleted", {"name": library_name, "entries_marked": removed_entries})
    return JSONResponse(
        {"deleted": library_name, "entries_marked": removed_entries},
        headers=cache_headers(snapshot),
    )


@router.get("/api/library/entries")
async def list_library_entries(
    status: Optional[str] = None,
    library: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Limit must be greater than zero")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset cannot be negative")

    entries = get_app_dependencies().library_entry_store.list_entries(status=status, library=library, limit=limit, offset=offset)
    return JSONResponse(jsonable_encoder([entry_to_response(entry) for entry in entries]))


@router.patch("/api/library/entries/{entry_id}")
async def update_entry_profile(entry_id: int, payload: EntryProfilePayload) -> JSONResponse:
    entry = get_app_dependencies().library_entry_store.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Library entry not found")
    profile = get_app_dependencies().profile_store.get(payload.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    updated = get_app_dependencies().library_entry_store.update_status(
        entry.path,
        entry.status,
        library=entry.library,
        profile=profile.name,
        profile_id=payload.profile_id,
        job_id=entry.last_job_id,
        output_path=entry.output_path,
        original_missing=entry.original_missing,
    )
    entry_payload = entry_to_response(updated)
    await NOTIFIER.broadcast({"type": "entry-update", "entry": entry_payload})
    return JSONResponse(jsonable_encoder(entry_payload))


@router.post("/api/library/entries/{entry_id}/reprocess")
async def reprocess_entry(
    entry_id: int, payload: Optional[ReprocessPayload] = None
) -> JSONResponse:
    entry = get_app_dependencies().library_entry_store.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Library entry not found")
    source = Path(entry.path)
    if not source.exists():
        updated = get_app_dependencies().library_entry_store.update_status(
            entry.path,
            LibraryStatus.REMOVED,
            message="Original missing",
            job_id=None,
            original_missing=True,
            profile=entry.profile,
            profile_id=entry.profile_id,
        )
        raise HTTPException(status_code=409, detail=entry_to_response(updated))
    profile_id = payload.profile_id if payload else None
    profile_name = entry.profile
    if profile_id is None:
        profile_id = entry.profile_id
    if profile_id is None:
        _, profile = get_library_profile(entry.library)
        profile_id = profile.id
        profile_name = profile.name
    else:
        profile = get_app_dependencies().profile_store.get(profile_id)
        if profile:
            profile_name = profile.name
    if profile_id is None:
        raise HTTPException(status_code=404, detail="Profile not available for entry")
    job = await get_app_dependencies().job_manager.add_job(
        entry.path,
        entry.library,
        profile_name,
        profile_id=profile_id,
        encoding=encoding_payload(profile_id),
        force=True,
    )
    updated = get_app_dependencies().library_entry_store.update_status(
        entry.path,
        LibraryStatus.PENDING,
        job_id=job.id,
        output_path=str(get_app_dependencies().job_manager.output_path(source)),
        original_missing=False,
        profile=profile_name,
        profile_id=profile_id,
    )
    entry_payload = entry_to_response(updated)
    await NOTIFIER.broadcast({"type": "entry-update", "entry": entry_payload})
    job_payload = job_to_response(job)
    await NOTIFIER.broadcast({"type": "job-update", "job": job_payload})
    payload = {"entry": entry_payload, "job": job_payload}
    return JSONResponse(jsonable_encoder(payload))


@router.post("/api/library/entries/{entry_id}/remove-original")
async def remove_original(entry_id: int) -> JSONResponse:
    entry = get_app_dependencies().library_entry_store.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Library entry not found")
    source = resolve_media_path(entry.path)
    output_path = Path(entry.output_path or get_app_dependencies().job_manager.output_path(source))
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HTTPException(status_code=409, detail="Converted output missing or empty")

    if not source.exists():
        updated = get_app_dependencies().library_entry_store.update_status(
            entry.path,
            LibraryStatus.REMOVED,
            original_missing=True,
            output_path=str(output_path),
            profile=entry.profile,
            profile_id=entry.profile_id,
        )
        entry_payload = entry_to_response(updated)
        await NOTIFIER.broadcast({"type": "entry-update", "entry": entry_payload})
        return JSONResponse(jsonable_encoder({"entry": entry_payload}))

    try:
        source.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    updated = get_app_dependencies().library_entry_store.update_status(
        entry.path,
        LibraryStatus.REMOVED,
        job_id=entry.last_job_id,
        output_path=str(output_path),
        original_missing=True,
        profile=entry.profile,
        profile_id=entry.profile_id,
    )
    entry_payload = entry_to_response(updated)
    await NOTIFIER.broadcast({"type": "entry-update", "entry": entry_payload})
    return JSONResponse(jsonable_encoder({"entry": entry_payload}))


@router.post("/api/scan")
async def manual_scan(payload: ScanRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    libraries = get_library_map()
    if payload.library:
        if payload.library not in libraries:
            raise HTTPException(status_code=404, detail="Library not found")
        target_libs = {payload.library: libraries[payload.library]}
    else:
        target_libs = libraries

    scheduled: List[str] = []
    for name, library in target_libs.items():
        root_path = payload.root or library.root
        profile = get_app_dependencies().profile_store.get(library.profile_id)
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


@router.post("/api/events")
async def handle_event(payload: EventBatch | EventPayload) -> JSONResponse:
    events = payload.events if isinstance(payload, EventBatch) else [payload]
    processed: List[Dict[str, Any]] = []

    for event in events:
        result = await process_event_payload(event)
        if result:
            processed.append(result)

    return JSONResponse(jsonable_encoder({"processed": processed, "count": len(processed)}))