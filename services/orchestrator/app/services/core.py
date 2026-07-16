import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

from .. import jobs
from ..dependencies import (
    NOTIFIER,
    get_app_dependencies,
    get_library_map,
)
from ..job_history import JobHistoryEntry, JobHistoryStatus
from ..library_entries import EntryUpdate, LibraryEntry, LibraryStatus
from ..profiles import EncodingProfile, LibraryConfig
from ..schemas import EventPayload, LibraryEntryResponse
from ..utils import (
    LIBRARY_ROOT_PREFIXES,
    normalize_display_path,
    resolve_media_path,
)

LOGGER = logging.getLogger("orchestrator.core")


def _job_elapsed_seconds(job: jobs.Job) -> int:
    start = job.started_at
    if not start:
        return 0
    if job.status in {jobs.JobStatus.COMPLETED, jobs.JobStatus.FAILED}:
        end = job.updated_at or datetime.now(timezone.utc)
    else:
        end = datetime.now(timezone.utc)
    try:
        return max(0, int((end - start).total_seconds()))
    except Exception:  # noqa: BLE001
        return 0


def job_to_response(job: jobs.Job) -> Dict[str, Any]:
    payload = job.model_dump()
    payload["path"] = normalize_display_path(payload.get("path"))
    pipeline = payload.get("pipeline") or {}
    if not pipeline and payload.get("encoding"):
        pipeline = (payload["encoding"] or {}).get("pipeline") or {}
    payload["pipeline"] = pipeline or None
    for field in ("decode_type", "scale_type", "encode_type"):
        if field not in payload and pipeline.get(field):
            payload[field] = pipeline.get(field)
    payload["elapsed_seconds"] = _job_elapsed_seconds(job)
    return payload


def entry_to_response(entry: LibraryEntry) -> Dict[str, Any]:
    payload = LibraryEntryResponse.model_validate(entry).model_dump()
    payload["path"] = normalize_display_path(payload.get("path"))
    if payload.get("output_path"):
        payload["output_path"] = normalize_display_path(payload.get("output_path"))
    payload["compliance"] = None
    detail = getattr(entry, "compliance_detail", None)
    if detail:
        try:
            payload["compliance"] = json.loads(detail)
        except json.JSONDecodeError:
            payload["compliance"] = None
    return payload


def record_job_history(
    job: jobs.Job, status: str, message: Optional[str] = None, *, completed: bool = False
) -> None:
    completed_at = datetime.now(timezone.utc) if completed else None
    try:
        get_app_dependencies().job_history_store.record(
            JobHistoryEntry(
                job_id=job.id,
                path=job.path,
                library=job.library,
                profile=job.profile,
                status=status,
                job_type=getattr(job, "job_type", "convert") or "convert",
                message=message,
                started_at=job.created_at,
                completed_at=completed_at,
            )
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Failed to persist job history for %s: %s", job.id[:8], exc)


def sync_entry_from_job(
    job: jobs.Job, status: str, message: Optional[str] = None
) -> Optional[LibraryEntry]:
    source = resolve_media_path(job.path)
    original_missing = not source.exists()
    final_status = status
    if status == LibraryStatus.CONVERTED and original_missing:
        final_status = LibraryStatus.REMOVED
    pipeline = job.pipeline or (job.encoding or {}).get("pipeline") or {}
    compliance = getattr(job, "compliance", None)
    compliance_kwargs: Dict[str, Any] = {}
    if compliance:
        compliance_kwargs = {
            "output_compliant": bool(compliance.get("compliant")),
            "compliance_detail": json.dumps(compliance),
        }
    return get_app_dependencies().library_entry_store.safe_update_status(
        job.path,
        final_status,
        library=job.library,
        profile=job.profile,
        profile_id=job.profile_id,
        message=message,
        job_id=job.id,
        output_path=str(get_app_dependencies().job_manager.output_path(source)),
        original_missing=original_missing,
        decode_type=pipeline.get("decode_type"),
        scale_type=pipeline.get("scale_type"),
        encode_type=pipeline.get("encode_type"),
        **compliance_kwargs,
    )


def encoding_payload(profile_id: int) -> Dict[str, Any]:
    profile = get_app_dependencies().profile_store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    definition = {}
    try:
        definition = json.loads(profile.definition or "{}")  # type: ignore
    except json.JSONDecodeError:
        definition = {}
    gpu = definition.get("gpu") or profile.to_payload().get("gpu", {})
    cpu = definition.get("cpu") or gpu
    return {
        "name": profile.name,
        "gpu": gpu,
        "cpu": cpu,
        "pipeline": {
            "decode_type": "gpu",
            "scale_type": "gpu",
            "encode_type": "gpu",
            "attempt": 1,
            "max_attempts": len(jobs.PIPELINE_SEQUENCE),
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


def should_track_file(path: Path | str) -> bool:
    resolved = resolve_media_path(path)
    if "sample" in str(resolved).lower():
        return False
    return (
        resolved.is_file()
        and resolved.suffix.lower() in get_app_dependencies().job_manager.video_extensions
        and "-chromecast" not in resolved.stem.lower()
    )


def entry_status_for_path(path: Path | str) -> Tuple[str, Path, bool]:
    resolved = resolve_media_path(path)
    output_path = get_app_dependencies().job_manager.output_path(resolved)
    if not resolved.exists():
        return LibraryStatus.REMOVED, output_path, False
    if get_app_dependencies().job_manager.is_converted(resolved, log=False):
        return LibraryStatus.CONVERTED, output_path, True
    return LibraryStatus.PENDING, output_path, True


async def record_library_entry(
    library_name: str,
    path: Path | str,
    profile: str,
    profile_id: int,
    *,
    emit_log: bool = True,
) -> Tuple[LibraryEntry, Optional[jobs.Job]]:
    resolved_path = resolve_media_path(path)

    # Offload blocking status check
    status, output_path, original_exists = await asyncio.to_thread(
        entry_status_for_path, resolved_path
    )

    entry = get_app_dependencies().library_entry_store.upsert(
        EntryUpdate(
            path=str(resolved_path),
            library=library_name,
            profile=profile,
            profile_id=profile_id,
            status=status,
            output_path=str(output_path),
            original_missing=not original_exists,
        ),
    )
    if status == LibraryStatus.PENDING:
        job = await get_app_dependencies().job_manager.add_job(
            str(resolved_path),
            library_name,
            profile,
            profile_id=profile_id,
            encoding=encoding_payload(profile_id),
            emit_log=emit_log,
        )
        get_app_dependencies().library_entry_store.attach_job(entry.id, job.id)  # type: ignore
        record_job_history(job, JobHistoryStatus.PENDING)
        return entry, job
    if status == LibraryStatus.CONVERTED and entry.output_compliant is None:
        # Existing output without a compliance verdict (e.g. converted before
        # compliance tracking existed): queue a verification so the GUI can
        # show whether it is Chromecast-safe. Deduplicated per path in Redis.
        verify_job = await get_app_dependencies().job_manager.add_verify_job(
            str(resolved_path), library_name, profile, profile_id=profile_id
        )
        if verify_job:
            record_job_history(verify_job, JobHistoryStatus.PENDING)
            return entry, verify_job
    return entry, None


def get_library_profile(library_name: str) -> Tuple[LibraryConfig, EncodingProfile]:
    library = get_app_dependencies().library_config_store.get(library_name)
    if library is None:
        raise HTTPException(status_code=404, detail="Library not found")
    profile = get_app_dependencies().profile_store.get(library.profile_id)  # type: ignore
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

    path = resolve_media_path(payload.path)
    if payload.is_directory:
        LOGGER.debug("Ignoring directory event for %s", path)
        return None

    if event_type == "deleted":
        entry = get_app_dependencies().library_entry_store.update_status(
            str(path),
            LibraryStatus.REMOVED,
            library=library.name,  # type: ignore
            profile=profile.name,  # type: ignore
            profile_id=profile.id,  # type: ignore
            output_path=str(get_app_dependencies().job_manager.output_path(path)),
            original_missing=True,
        )
        entry_payload = entry_to_response(entry)
        await NOTIFIER.broadcast({"type": "entry-update", "entry": entry_payload})
        return {"entry": entry_payload, "event": event_type}

    if not should_track_file(path):
        LOGGER.debug("Ignoring non-media event for %s", path)
        return None

    try:
        entry, job = await record_library_entry(
            library.name, path, profile.name, profile.id  # type: ignore
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    entry_payload = entry_to_response(entry)
    await NOTIFIER.broadcast({"type": "entry-update", "entry": entry_payload})
    response: Dict[str, Any] = {"entry": entry_payload, "event": event_type}
    if job:
        job_payload = job_to_response(job)
        response["job"] = job_payload
        await NOTIFIER.broadcast({"type": "job-update", "job": job_payload})
    return response


def _reconcile_scan_helper(root_path: Path) -> Tuple[Set[str], List[Path]]:
    seen: Set[str] = set()
    valid_entries: List[Path] = []
    try:
        for entry in root_path.rglob("*.*"):
            resolved_entry = resolve_media_path(entry)
            if not should_track_file(resolved_entry):
                continue
            seen.add(str(resolved_entry))
            valid_entries.append(resolved_entry)
    except OSError:
        pass
    return seen, valid_entries


async def reconcile_library(library_name: str, root: str, profile: str, profile_id: int) -> None:
    root_path = resolve_media_path(root)
    if not root_path.exists():
        LOGGER.warning("Library root %s missing; marking entries removed", root_path)
        get_app_dependencies().library_entry_store.mark_missing(library_name, set())
        return

    # Offload recursive scan
    seen, valid_entries = await asyncio.to_thread(_reconcile_scan_helper, root_path)

    queued = 0
    converted = 0
    tracked_pending = 0

    for resolved_entry in valid_entries:
        # emit_log=False to reduce noise during bulk scans
        library_entry, job = await record_library_entry(
            library_name, resolved_entry, profile, profile_id, emit_log=False
        )
        if job:
            queued += 1
        elif library_entry.status == LibraryStatus.CONVERTED:
            converted += 1
        elif library_entry.status == LibraryStatus.PENDING:
            tracked_pending += 1

    removed = get_app_dependencies().library_entry_store.mark_missing(library_name, seen)
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
