import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

from .. import jobs
from ..dependencies import (
    JOB_HISTORY_STORE,
    LIBRARY_CONFIG_STORE,
    LIBRARY_STORE,
    PROFILE_STORE,
    get_library_map,
    job_manager,
)
from ..job_history import JobHistoryEntry, JobHistoryStatus
from ..library_entries import EntryUpdate, LibraryEntry, LibraryStatus
from ..profiles import EncodingProfile, LibraryConfig
from ..schemas import EventPayload, LibraryEntryResponse
from ..utils import (
    LIBRARY_ROOT_PREFIXES,
    normalize_display_path,
)

LOGGER = logging.getLogger("orchestrator.core")


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


def job_to_response(job: jobs.Job) -> Dict[str, Any]:
    payload = job.model_dump()
    payload["path"] = normalize_display_path(payload.get("path"))
    payload["elapsed_seconds"] = _job_elapsed_seconds(job)
    return payload


def entry_to_response(entry: LibraryEntry) -> Dict[str, Any]:
    payload = LibraryEntryResponse.model_validate(entry).model_dump()
    payload["path"] = normalize_display_path(payload.get("path"))
    if payload.get("output_path"):
        payload["output_path"] = normalize_display_path(payload.get("output_path"))
    return payload


def record_job_history(
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


def sync_entry_from_job(job: jobs.Job, status: str, message: Optional[str] = None) -> None:
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


def find_library_for_path(path: str) -> Optional[str]:
    try:
        normalized = Path(path).resolve()
    except FileNotFoundError:
        normalized = Path(path)
    for name, library in get_library_map().items():
        library_root = Path(library.root)
        for candidate_root in _candidate_library_roots(library_root):
            if normalized.is_relative_to(candidate_root):
                return name
    return None


def should_track_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in job_manager.video_extensions
        and "-chromecast" not in path.stem.lower()
    )


def entry_status_for_path(path: Path) -> Tuple[str, Path, bool]:
    output_path = job_manager.output_path(path)
    if not path.exists():
        return LibraryStatus.REMOVED, output_path, False
    if job_manager.is_converted(path, log=False):
        return LibraryStatus.CONVERTED, output_path, True
    return LibraryStatus.PENDING, output_path, True


async def record_library_entry(
    library_name: str,
    path: Path,
    profile: str,
    profile_id: int,
    *,
    emit_log: bool = True,
) -> Tuple[LibraryEntry, Optional[jobs.Job]]:
    status, output_path, original_exists = entry_status_for_path(path)
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
        record_job_history(job, JobHistoryStatus.PENDING)
        return entry, job
    return entry, None


def get_library_profile(library_name: str) -> Tuple[LibraryConfig, EncodingProfile]:
    library = LIBRARY_CONFIG_STORE.get(library_name)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    profile = PROFILE_STORE.get(library.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found for library")
    return library, profile


async def process_event_payload(payload: EventPayload) -> Optional[Dict[str, Any]]:
    event_type = payload.event.lower()
    if event_type not in {"created", "modified", "deleted"}:
        raise HTTPException(status_code=400, detail="Unsupported event type")
    library_name = payload.library or find_library_for_path(payload.path)
    if not library_name:
        raise HTTPException(status_code=400, detail="Library could not be determined")
    library, profile = get_library_profile(library_name)

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
        return {"entry": entry_to_response(entry), "event": event_type}

    if not should_track_file(path):
        LOGGER.debug("Ignoring non-media event for %s", path)
        return None

    try:
        entry, job = await record_library_entry(library.name, path, profile.name, profile.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    response: Dict[str, Any] = {"entry": entry_to_response(entry), "event": event_type}
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
        if not should_track_file(entry):
            continue
        seen.add(str(entry))
        library_entry, job = await record_library_entry(
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
