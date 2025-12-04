import logging
import re
from functools import wraps

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from .. import jobs
from ..dependencies import NOTIFIER, get_app_dependencies, worker_metrics_summary
from ..schemas import JobAckPayload, JobStatusPayload, QueuePauseRequest
from ..services.core import (
    JobHistoryStatus,
    LibraryStatus,
    entry_to_response,
    job_to_response,
    record_job_history,
    sync_entry_from_job,
)

LOGGER = logging.getLogger("orchestrator.jobs")
router = APIRouter()
NON_RETRYABLE_CATEGORIES = {"unsupported_bit_depth", "unsupported_pixel_format", "corrupt_input"}


async def _broadcast_entry_update(entry) -> None:
    if not entry:
        return
    await NOTIFIER.broadcast({"type": "entry-update", "entry": entry_to_response(entry)})


async def _broadcast_job_update(job: jobs.Job) -> dict:
    payload = job_to_response(job)
    await NOTIFIER.broadcast({"type": "job-update", "job": payload})
    return payload


async def _respond_with_updates(job: jobs.Job, entry) -> JSONResponse:
    await _broadcast_entry_update(entry)
    payload = await _broadcast_job_update(job)
    return JSONResponse(jsonable_encoder(payload))


def classify_ffmpeg_error(logs: list[str], return_code: int) -> dict[str, str | bool | int]:
    patterns = [
        (r"10[- ]?bit|yuv420p10", "unsupported_bit_depth", False),
        (r"pix_fmt [^ ]+ is not supported", "unsupported_pixel_format", False),
        (r"device busy|resource temporarily unavailable", "device_busy", True),
        (r"nvenc.+not available|no capable devices", "nvenc_unavailable", True),
        (r"Invalid data found when processing input", "corrupt_input", False),
        (r"moov atom not found", "incomplete_file", True),
        (r"No such filter: 'tonemap_cuda'", "unsupported_filter", False),
    ]
    tail = "\n".join(logs[-5:]) if logs else ""
    for pattern, category, retryable in patterns:
        if re.search(pattern, tail, re.IGNORECASE):
            return {
                "category": category,
                "retryable": retryable,
                "message": tail.splitlines()[-1] if tail else "",
                "return_code": return_code,
            }
    return {
        "category": "unknown",
        "retryable": False,
        "message": logs[-1] if logs else "",
        "return_code": return_code,
    }


def guard_job_queue_errors(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except RedisError as exc:  # pragma: no cover - exercised via FastAPI tests
            LOGGER.error("Job queue unavailable: %s", exc)
            raise HTTPException(status_code=503, detail="Job queue unavailable") from exc

    return wrapper


@router.get("/api/jobs")
@guard_job_queue_errors
async def list_jobs() -> JSONResponse:
    jobs_list = await get_app_dependencies().job_manager.list_jobs()
    return JSONResponse(jsonable_encoder([job_to_response(job) for job in jobs_list]))


@router.post("/api/jobs/clear")
@guard_job_queue_errors
async def clear_completed_jobs() -> JSONResponse:
    removed = await get_app_dependencies().job_manager.clear_processed()
    return JSONResponse({"removed": removed})


@router.post("/api/jobs/purge-inactive")
@guard_job_queue_errors
async def purge_inactive_jobs() -> JSONResponse:
    result = await get_app_dependencies().job_manager.purge_inactive_jobs()
    return JSONResponse(result)


@router.get("/api/jobs/next")
@guard_job_queue_errors
async def next_job(
    worker_id: str = Query("api", description="ID of the worker claiming the job"),
) -> JSONResponse:
    queue_state = await get_app_dependencies().job_manager.queue_state()
    if queue_state["paused"]:
        return JSONResponse(queue_state | {"detail": "Queue paused"}, status_code=409)
    claimed = await get_app_dependencies().job_manager.acquire_next(worker_id)
    if claimed is None:
        raise HTTPException(status_code=204, detail="No jobs available")
    delivery_id, job = claimed
    entry = sync_entry_from_job(job, LibraryStatus.CONVERTING)
    await _broadcast_entry_update(entry)
    record_job_history(job, JobHistoryStatus.RUNNING)
    payload = await _broadcast_job_update(job)
    payload["delivery_id"] = delivery_id
    return JSONResponse(jsonable_encoder(payload))


@router.post("/api/jobs/{job_id}/status")
@guard_job_queue_errors
async def update_job_status(job_id: str, payload: JobStatusPayload) -> JSONResponse:
    try:
        job = await get_app_dependencies().job_manager.update_job(
            job_id, jobs.JobStatusUpdate(**payload.model_dump())
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Job not found")
    if payload.status == jobs.JobStatus.FAILED:
        classification = classify_ffmpeg_error(payload.logs or [], payload.return_code or -1)
        retry_pipeline = None
        if (
            job.attempt < job.max_attempts
            and classification.get("category") not in NON_RETRYABLE_CATEGORIES
        ):
            retry_pipeline = jobs.pipeline_for_attempt(
                job.attempt + 1, max_attempts=job.max_attempts
            )
        if retry_pipeline:
            retry_message = (
                f"Retry {job.attempt + 1}/{job.max_attempts}: "
                f"{retry_pipeline.get('decode_type', 'cpu').upper()} decode / "
                f"{retry_pipeline.get('scale_type', 'cpu').upper()} scale / "
                f"{retry_pipeline.get('encode_type', 'cpu').upper()} encode"
            )
            job = await get_app_dependencies().job_manager.schedule_retry(
                job, retry_pipeline, message=retry_message
            )
            entry = sync_entry_from_job(job, LibraryStatus.PENDING, retry_message)
            record_job_history(job, JobHistoryStatus.RUNNING, retry_message, completed=False)
            return await _respond_with_updates(job, entry)
        failure_message = payload.message or classification.get("message")
        entry = sync_entry_from_job(job, LibraryStatus.FAILED, failure_message)
        record_job_history(job, payload.status, failure_message, completed=True)
        return await _respond_with_updates(job, entry)

    completed = payload.status == jobs.JobStatus.COMPLETED
    entry = None
    if payload.status == jobs.JobStatus.RUNNING:
        entry = sync_entry_from_job(job, LibraryStatus.CONVERTING, payload.message)
    elif payload.status == jobs.JobStatus.COMPLETED:
        if getattr(job, "job_type", "convert") == "delete":
            entry = sync_entry_from_job(job, LibraryStatus.REMOVED, payload.message)
        else:
            entry = sync_entry_from_job(job, LibraryStatus.CONVERTED, payload.message)
    record_job_history(job, payload.status, payload.message, completed=completed)
    tracked_entry = entry if completed or payload.status == jobs.JobStatus.RUNNING else None
    return await _respond_with_updates(job, tracked_entry)


@router.post("/api/jobs/{job_id}/ack")
@guard_job_queue_errors
async def acknowledge_job(job_id: str, payload: JobAckPayload) -> JSONResponse:
    await get_app_dependencies().job_manager.acknowledge(payload.delivery_id, job_id)
    return JSONResponse({"acknowledged": True})


@router.get("/api/queue/state")
@guard_job_queue_errors
async def queue_state() -> JSONResponse:
    state = await get_app_dependencies().job_manager.queue_state()
    state["workers"] = worker_metrics_summary()
    return JSONResponse(state)


@router.post("/api/queue/pause")
@guard_job_queue_errors
async def pause_queue(payload: QueuePauseRequest) -> JSONResponse:
    await get_app_dependencies().job_manager.pause(payload.reason)
    LOGGER.warning("Job queue paused: %s", payload.reason or "no reason provided")
    return JSONResponse(await get_app_dependencies().job_manager.queue_state())


@router.post("/api/queue/resume")
@guard_job_queue_errors
async def resume_queue() -> JSONResponse:
    await get_app_dependencies().job_manager.resume()
    LOGGER.info("Job queue resumed")
    return JSONResponse(await get_app_dependencies().job_manager.queue_state())
