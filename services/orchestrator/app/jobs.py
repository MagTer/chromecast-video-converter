from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import redis.asyncio as redis
from pydantic import BaseModel, Field

from .context import get_request_id
from .utils import resolve_media_path


class JobStatus(str):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    job_type: str = "convert"
    path: str
    library: str
    profile: str
    profile_id: Optional[int] = None
    encoding: Optional[Dict[str, Any]] = None
    pipeline: Optional[Dict[str, Any]] = None
    attempt: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=5, ge=1)
    status: str = JobStatus.PENDING
    worker_id: Optional[str] = None
    request_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    progress: int = 0
    message: Optional[str] = None
    force: bool = Field(default=False)

    class Config:
        json_encoders = {datetime: lambda value: value.isoformat()}


PIPELINE_SEQUENCE: list[Dict[str, Any]] = [
    {"decode_type": "gpu", "scale_type": "gpu", "encode_type": "gpu"},
    {"decode_type": "gpu", "scale_type": "gpu", "encode_type": "gpu"},
    {"decode_type": "cpu", "scale_type": "gpu", "encode_type": "gpu"},
    {"decode_type": "cpu", "scale_type": "cpu", "encode_type": "gpu"},
    {"decode_type": "cpu", "scale_type": "cpu", "encode_type": "cpu"},
]


def pipeline_for_attempt(attempt: int, *, max_attempts: int = 5) -> Dict[str, Any]:
    idx = min(max(attempt - 1, 0), len(PIPELINE_SEQUENCE) - 1)
    base = dict(PIPELINE_SEQUENCE[idx])
    base["attempt"] = attempt
    base["max_attempts"] = max_attempts
    return base


class JobStatusUpdate(BaseModel):
    status: str
    progress: Optional[int] = None
    message: Optional[str] = None
    return_code: Optional[int] = None
    logs: Optional[list] = None
    pipeline: Optional[Dict[str, Any]] = None


class JobManager:
    _PATH_RESERVATION_ATTEMPTS = 4
    _JOB_FETCH_RETRIES = 8
    _JOB_FETCH_DELAY = 0.05

    def __init__(self, redis_url: str, *, visibility_timeout: int = 300) -> None:
        self._logger = logging.getLogger(__name__)
        self._video_extensions = {".mp4", ".m4v", ".mov", ".mkv", ".ts", ".flv"}
        self._redis_url = redis_url
        self._redis = None
        self._stream = os.environ.get("JOB_QUEUE_STREAM", "job_queue")
        self._group = os.environ.get("JOB_QUEUE_GROUP", "workers")
        self._visibility_timeout = visibility_timeout
        self._paused_key = f"{self._stream}:paused"
        self._pause_reason_key = f"{self._stream}:pause_reason"
        self._path_index = f"{self._stream}:paths"
        self._path_lookup_prefix = f"{self._stream}:path"
        self._job_index = f"{self._stream}:index"
        self._job_data_prefix = f"{self._stream}:job"
        self._ensure_group_lock = asyncio.Lock()

    def _canonical_path(self, path: str | Path) -> str:
        return str(resolve_media_path(path))

    def _output_path(self, source: Path) -> Path:
        canonical = Path(self._canonical_path(source))
        return canonical.parent / f"{canonical.stem}-chromecast.mp4"

    def _already_converted(self, source: Path, *, log: bool = True) -> bool:
        canonical_source = Path(self._canonical_path(source))
        output_path = self._output_path(canonical_source)
        if not output_path.exists():
            return False
        try:
            output_stat = output_path.stat()
            source_mtime = canonical_source.stat().st_mtime
        except OSError:
            return True
        if output_stat.st_size == 0:
            return False
        if output_stat.st_mtime >= source_mtime:
            if log:
                self._logger.info(
                    "Skipping already converted file %s (output: %s)",
                    canonical_source,
                    output_path,
                )
            return True
        return False

    @property
    def video_extensions(self) -> set[str]:
        return set(self._video_extensions)

    def output_path(self, source: Path) -> Path:
        canonical_source = Path(self._canonical_path(source))
        return self._output_path(canonical_source)

    def is_converted(self, source: Path, *, log: bool = False) -> bool:
        canonical_source = Path(self._canonical_path(source))
        return self._already_converted(canonical_source, log=log)

    async def initialize(self) -> None:
        async with self._ensure_group_lock:
            if self._redis is None:
                self._redis = redis.from_url(self._redis_url, decode_responses=True)
            try:
                await self._redis.xgroup_create(self._stream, self._group, id="0-0", mkstream=True)
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    def _job_key(self, job_id: str) -> str:
        return f"{self._job_data_prefix}:{job_id}"

    def _path_key(self, path: str) -> str:
        return f"{self._path_lookup_prefix}:{path}"

    async def _fetch_existing_job(self, job_id: Optional[str]) -> Optional[Job]:
        if not job_id:
            return None
        job_key = self._job_key(job_id)
        for _ in range(self._JOB_FETCH_RETRIES):
            data = await self._redis.hgetall(job_key)
            if data:
                return self._decode_job(data)
            await asyncio.sleep(self._JOB_FETCH_DELAY)
        return None

    async def _set_nx(self, key: str, value: str) -> bool:
        try:
            return await self._redis.set(key, value, nx=True)
        except TypeError:
            exists = await self._redis.get(key)
            if exists:
                return False
            await self._redis.set(key, value)
            return True
        except redis.ResponseError:
            return False

    async def _reserve_path_for_job(self, path: str, job_id: str, *, force: bool) -> Optional[Job]:
        path_key = self._path_key(path)
        if force:
            await self._redis.set(path_key, job_id)
            return None

        reserved = False
        for attempt in range(self._PATH_RESERVATION_ATTEMPTS):
            reserved = await self._set_nx(path_key, job_id)
            if reserved:
                break
            existing_job_id = await self._redis.get(path_key)
            existing_job = await self._fetch_existing_job(existing_job_id)
            if existing_job:
                self._logger.debug("Job already tracked for %s", path)
                return existing_job
            if attempt < self._PATH_RESERVATION_ATTEMPTS - 1:
                self._logger.warning(
                    "Clearing stale reservation for %s (job=%s)", path, existing_job_id
                )
                await self._redis.delete(path_key)

        if not reserved:
            self._logger.warning("Unable to reserve job slot for %s; forcing reservation", path)
            await self._redis.set(path_key, job_id)
        return None

    def _encode_job(self, job: Job) -> dict[str, str]:
        payload = job.model_dump()
        payload["created_at"] = payload["created_at"].isoformat()
        payload["updated_at"] = payload["updated_at"].isoformat()
        if payload.get("started_at"):
            payload["started_at"] = payload["started_at"].isoformat()
        encoded: dict[str, str] = {}
        for key, value in payload.items():
            if value is None:
                encoded[key] = ""
            elif isinstance(value, (dict, list)):
                encoded[key] = json.dumps(value)
            else:
                encoded[key] = str(value)
        return encoded

    def _decode_job(self, data: dict[str, str]) -> Job:
        if not data:
            raise KeyError("job not found")
        parsed: Dict[str, Any] = {}
        for key, value in data.items():
            if key in {"created_at", "updated_at", "started_at"}:
                parsed[key] = datetime.fromisoformat(value) if value else None
            elif key == "progress":
                parsed[key] = int(value) if value else 0
            elif key == "profile_id":
                parsed[key] = int(value) if value else None
            elif key in {"encoding", "pipeline"}:
                parsed[key] = json.loads(value) if value else None
            else:
                parsed[key] = value or None
        return Job(**parsed)

    async def _acquire_stalled(self, consumer: str) -> tuple[Optional[str], Optional[Job]]:
        try:
            response = await self._redis.xautoclaim(
                self._stream,
                self._group,
                consumer,
                min_idle_time=self._visibility_timeout * 1000,
                start_id="0-0",
                count=1,
            )
            if isinstance(response, tuple):
                if len(response) == 3:  # redis-py>=5 returns (next_id, messages, deleted)
                    _next_id, messages, _deleted = response
                elif len(response) == 2:
                    _next_id, messages = response
                else:  # pragma: no cover - defensive for unexpected shapes
                    _next_id, messages = response, []
            else:  # pragma: no cover - compatibility shim
                _next_id, messages = response, []
        except redis.ResponseError as exc:
            self._logger.debug("xautoclaim failed: %s", exc)
            return None, None
        if not messages:
            return None, None
        message_id, fields = messages[0]
        payload = json.loads(fields.get("payload", "{}"))
        job = self._decode_job(payload)

        # Zombie detection
        delivery_count_key = f"job:{job.id}:delivery_count"
        count = await self._redis.incr(delivery_count_key)
        if count > 3:
            self._logger.error("Job %s crashed repeatedly (count=%s); failing", job.id[:8], count)
            job.status = JobStatus.FAILED
            job.message = "Worker crashed repeatedly"
            job.updated_at = datetime.utcnow()
            await self._redis.hset(self._job_key(job.id), mapping=self._encode_job(job))
            await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})
            await self._redis.xack(self._stream, self._group, message_id)
            await self._redis.delete(delivery_count_key)
            return None, None

        job.status = JobStatus.RUNNING
        job.worker_id = consumer
        job.updated_at = datetime.utcnow()
        await self._redis.hset(self._job_key(job.id), mapping=self._encode_job(job))
        await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})
        self._logger.warning(
            "Re-claimed stalled job %s (path=%s) for consumer %s (attempt %s)",
            job.id[:8],
            job.path,
            consumer,
            count,
        )
        return message_id, job

    def _validate_job_path(self, source: Path, force: bool, emit_log: bool, path: str) -> None:
        if source.suffix.lower() not in self._video_extensions:
            raise ValueError("Unsupported media extension")
        if "-chromecast" in source.stem.lower():
            raise ValueError("Converted outputs are ignored")

        if force:
            output_path = self._output_path(source)
            if output_path.exists():
                try:
                    output_path.unlink()
                    self._logger.info("Deleted existing output %s for forced job", output_path)
                except OSError as exc:
                    self._logger.warning(
                        "Failed to delete existing output %s: %s", output_path, exc
                    )
        elif self._already_converted(source, log=emit_log):
            raise ValueError(f"Output already exists for {path}")

    async def add_job(
        self,
        path: str,
        library: str,
        profile: str,
        profile_id: Optional[int] = None,
        encoding: Optional[Dict[str, Any]] = None,
        force: bool = False,
        *,
        emit_log: bool = True,
    ) -> Job:
        await self.initialize()
        path = self._canonical_path(path)
        source = Path(path)
        self._validate_job_path(source, force, emit_log, path)

        pipeline = encoding.get("pipeline") if encoding else None
        max_attempts = 5
        attempt = 1
        if isinstance(pipeline, dict):
            max_attempts = int(pipeline.get("max_attempts", max_attempts) or max_attempts)
            attempt = int(pipeline.get("attempt", attempt) or attempt)
        else:
            pipeline = pipeline_for_attempt(attempt, max_attempts=max_attempts)
            if encoding is None:
                encoding = {}
            encoding["pipeline"] = pipeline

        job = Job(
            path=path,
            library=library,
            profile=profile,
            profile_id=profile_id,
            encoding=encoding,
            pipeline=pipeline,
            attempt=attempt,
            max_attempts=max_attempts,
            request_id=get_request_id(),
            job_type="convert",
            force=force,
        )
        existing_job = await self._reserve_path_for_job(path, job.id, force=force)
        if existing_job:
            return existing_job
        encoded = self._encode_job(job)
        await self._redis.hset(self._job_key(job.id), mapping=encoded)
        await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})
        await self._redis.sadd(self._path_index, path)
        await self._redis.set(self._path_key(path), job.id)
        await self._redis.xadd(
            self._stream, {"payload": json.dumps(encoded)}, maxlen=10_000, approximate=True
        )
        if emit_log:
            self._logger.info(
                "Queued job %s for %s (library=%s, profile=%s)",
                job.id[:8],
                path,
                library,
                profile,
            )
        return job

    async def add_delete_job(self, path: str) -> Job:
        await self.initialize()
        path = self._canonical_path(path)
        # No extension check or converted check for delete jobs; we want to delete what's asked.

        job = Job(
            path=path,
            library="system",
            profile="system-delete",
            job_type="delete",
            request_id=get_request_id(),
            pipeline={"max_attempts": 3},  # Simpler pipeline for delete
        )

        # No path reservation check? Or should we?
        # If a conversion is running, we probably shouldn't delete.
        # But "remove-original" usually happens after conversion or manual trigger.
        # Let's skip reservation for now to avoid complexity, or use a different key?
        # Actually, if we reserve, we prevent concurrent operations.
        # Let's use force=True behavior effectively or just unique ID.
        # But we want to track it.

        encoded = self._encode_job(job)
        await self._redis.hset(self._job_key(job.id), mapping=encoded)
        await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})
        # Do NOT add to _path_index or _path_key to avoid conflict with existing media tracking?
        # If we do, it might block re-scanning.
        # But `remove-original` implies the file IS there.

        await self._redis.xadd(
            self._stream, {"payload": json.dumps(encoded)}, maxlen=10_000, approximate=True
        )
        self._logger.info("Queued DELETE job %s for %s", job.id[:8], path)
        return job

    STALE_RUNNING_TIMEOUT = 300  # seconds

    async def _pending_message_map(self) -> dict[str, str]:
        pending_map: dict[str, str] = {}
        message_ids: list[str] = []
        if hasattr(self._redis, "xpending_range"):
            try:
                entries = await self._redis.xpending_range(
                    self._stream, self._group, "-", "+", 1000
                )
                for entry in entries:
                    if isinstance(entry, dict):
                        message_ids.append(entry.get("message_id", ""))
                    elif isinstance(entry, tuple):
                        message_ids.append(entry[0])
            except TypeError:
                pass
        else:
            message_ids = await self._pending_message_ids_from_xpending()
        for message_id in message_ids:
            if not message_id:
                continue
            records = await self._redis.xrange(self._stream, message_id, message_id)
            if not records:
                continue
            _, fields = records[0]
            payload = json.loads(fields.get("payload", "{}"))
            job_id = payload.get("id")
            if job_id:
                pending_map[job_id] = message_id
        return pending_map

    async def _pending_message_ids_from_xpending(self) -> list[str]:
        try:
            entries = await self._redis.xpending(self._stream, self._group, "-", "+", 1000)
        except Exception:
            return []
        message_ids: list[str] = []
        for entry in entries or []:
            if isinstance(entry, (list, tuple)) and entry:
                message_ids.append(entry[0])
        return message_ids

    async def _ack_pending_message(self, message_id: str) -> None:
        if not message_id:
            return
        await self._redis.xack(self._stream, self._group, message_id)
        await self._redis.xdel(self._stream, message_id)

    async def _stream_messages_by_job(self, batch_size: int = 500) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        if not hasattr(self._redis, "xrange"):
            return mapping
        start = "-"
        while True:
            entries = await self._redis.xrange(self._stream, start, "+", count=batch_size)
            if not entries:
                break
            for message_id, fields in entries:
                payload_raw = fields.get("payload")
                if not payload_raw:
                    continue
                try:
                    payload = json.loads(payload_raw)
                except json.JSONDecodeError:
                    continue
                job_id = payload.get("id")
                if job_id:
                    mapping.setdefault(job_id, []).append(message_id)
            if len(entries) < batch_size:
                break
            start = f"({entries[-1][0]}"
        return mapping

    async def _purge_stream_messages(self, running_job_ids: set[str]) -> tuple[int, int]:
        stream_deleted = 0
        pending_ack = 0
        start = "-"
        while True:
            entries = await self._redis.xrange(self._stream, start, "+", count=1000)
            if not entries:
                break

            for message_id, fields in entries:
                should_delete = False
                payload_raw = fields.get("payload")

                if not payload_raw:
                    should_delete = True
                else:
                    try:
                        payload = json.loads(payload_raw)
                        job_id = payload.get("id")
                        if not job_id or job_id not in running_job_ids:
                            should_delete = True
                    except (json.JSONDecodeError, AttributeError):
                        should_delete = True

                if should_delete:
                    await self._redis.xack(self._stream, self._group, message_id)
                    deleted = await self._redis.xdel(self._stream, message_id)
                    if deleted:
                        stream_deleted += deleted
                    pending_ack += 1

            if len(entries) < 1000:
                break
            start = f"({entries[-1][0]}"
        return stream_deleted, pending_ack

    async def _purge_ghost_pel_entries(self) -> int:
        pending_ack = 0
        if hasattr(self._redis, "xpending_range"):
            try:
                pending_entries = await self._redis.xpending_range(
                    self._stream, self._group, "-", "+", 10000
                )
                for entry in pending_entries:
                    msg_id = entry["message_id"]
                    exists = await self._redis.xrange(self._stream, msg_id, msg_id)
                    if not exists:
                        await self._redis.xack(self._stream, self._group, msg_id)
                        pending_ack += 1
            except Exception as e:
                self._logger.error("Failed to clean up PEL: %s", e)
        return pending_ack

    async def purge_inactive_jobs(self) -> dict[str, int]:
        await self.initialize()

        # 1. Identify running jobs and clean up inactive job metadata
        all_job_ids = await self._redis.zrange(self._job_index, 0, -1)
        running_job_ids = set()
        removed_jobs = 0

        for job_id in all_job_ids:
            data = await self._redis.hgetall(self._job_key(job_id))
            if not data:
                await self._redis.zrem(self._job_index, job_id)
                continue

            try:
                job = self._decode_job(data)
                if job.status == JobStatus.RUNNING:
                    running_job_ids.add(job.id)
                else:
                    await self._redis.delete(self._job_key(job.id))
                    await self._redis.zrem(self._job_index, job.id)
                    await self._redis.srem(self._path_index, job.path)
                    await self._redis.delete(self._path_key(job.path))
                    removed_jobs += 1
            except Exception:
                await self._redis.delete(self._job_key(job_id))
                await self._redis.zrem(self._job_index, job_id)
                removed_jobs += 1

        # 2. Clean up Stream
        stream_deleted, stream_pending_ack = await self._purge_stream_messages(running_job_ids)

        # 3. Clean up Ghost PEL entries
        pel_pending_ack = await self._purge_ghost_pel_entries()

        return {
            "removed_jobs": removed_jobs,
            "pending_messages_acknowledged": stream_pending_ack + pel_pending_ack,
            "stream_entries_deleted": stream_deleted,
        }

    async def _handle_stale_job(self, job_id: str, job: Job, pending_map: dict[str, str]) -> None:
        threshold = datetime.utcnow() - timedelta(seconds=self.STALE_RUNNING_TIMEOUT)
        if not job.updated_at or job.updated_at > threshold:
            return
        self._logger.warning("Stale job detected: %s (status=%s)", job_id[:8], job.status)
        if job.attempt < job.max_attempts:
            pipeline = job.pipeline or {}
            await self.schedule_retry(job, pipeline, message="Worker lost; retrying automatically")
            message_id = pending_map.get(job_id)
            await self._ack_pending_message(message_id)
        else:
            job.status = JobStatus.FAILED
            job.progress = 0
            job.message = "Worker unavailable; job marked failed"
            job.updated_at = datetime.utcnow()
            await self._redis.hset(self._job_key(job_id), mapping=self._encode_job(job))
            await self._redis.zadd(self._job_index, {job_id: job.updated_at.timestamp()})
            self._logger.warning("Marked job %s as failed after missing worker", job_id[:8])

    async def _cleanup_stale_jobs(self, limit: int = 200) -> None:
        job_ids = await self._redis.zrange(self._job_index, 0, limit - 1)
        pending_map = await self._pending_message_map()
        for job_id in job_ids:
            data = await self._redis.hgetall(self._job_key(job_id))
            if not data:
                continue
            job = self._decode_job(data)
            if job.status == JobStatus.RUNNING:
                await self._handle_stale_job(job_id, job, pending_map)

    async def list_jobs(self, limit: int = 200) -> List[Job]:
        await self.initialize()
        await self._cleanup_stale_jobs(limit=limit)
        job_ids = await self._redis.zrevrange(self._job_index, 0, limit - 1)
        jobs: List[Job] = []
        for job_id in job_ids:
            data = await self._redis.hgetall(self._job_key(job_id))
            if data:
                job = self._decode_job(data)
                if job.status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
                    jobs.append(job)
        return jobs

    async def check_dead_workers(self, active_workers: Dict[str, Any]) -> int:
        await self.initialize()
        jobs = await self.list_jobs(limit=1000)
        pending_map = await self._pending_message_map()
        failed_count = 0
        now = datetime.utcnow()
        threshold_seconds = 90

        for job in jobs:
            if job.status != JobStatus.RUNNING or not job.worker_id:
                continue

            worker = active_workers.get(job.worker_id)
            is_dead = False
            reason = ""

            if not worker:
                if job.updated_at < now - timedelta(seconds=threshold_seconds):
                    is_dead = True
                    reason = f"Worker {job.worker_id} missing"
            else:
                last_seen = worker.checked_at
                if last_seen.tzinfo:
                    last_seen = last_seen.replace(tzinfo=None)
                if (now - last_seen).total_seconds() > threshold_seconds:
                    is_dead = True
                    reason = f"Worker {job.worker_id} timed out"

            if is_dead:
                self._logger.warning("Failing job %s: %s", job.id, reason)

                job.status = JobStatus.FAILED
                job.message = f"Worker Failure: {reason}"
                job.progress = 0
                job.updated_at = datetime.utcnow()

                await self._redis.hset(self._job_key(job.id), mapping=self._encode_job(job))
                await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})

                msg_id = pending_map.get(job.id)
                if msg_id:
                    await self._ack_pending_message(msg_id)

                failed_count += 1

        return failed_count

    async def acquire_next(self, consumer: str) -> Optional[tuple[str, Job]]:
        await self.initialize()
        paused_state = await self.queue_state()
        if paused_state["paused"]:
            return None

        pending_id, pending_job = await self._acquire_stalled(consumer)
        if pending_job:
            return pending_id, pending_job

        result = await self._redis.xreadgroup(
            self._group, consumer, {self._stream: ">"}, count=1, block=1000
        )
        if not result:
            return None
        _, messages = result[0]
        message_id, body = messages[0]
        payload = json.loads(body.get("payload", "{}"))
        job = self._decode_job(payload)
        job.status = JobStatus.RUNNING
        job.progress = job.progress or 0
        job.worker_id = consumer
        job.started_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        await self._redis.hset(self._job_key(job.id), mapping=self._encode_job(job))
        await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})
        self._logger.info(
            "Handing off job %s to consumer %s (path=%s, library=%s)",
            job.id[:8],
            consumer,
            job.path,
            job.library,
        )
        return message_id, job

    async def queue_state(self) -> Dict[str, object]:
        await self.initialize()
        paused = await self._redis.get(self._paused_key)
        reason = await self._redis.get(self._pause_reason_key)
        depth = await self._redis.xlen(self._stream)
        pending_summary = await self._redis.xpending(self._stream, self._group)
        pending = pending_summary["pending"] if pending_summary else 0
        return {
            "paused": bool(int(paused)) if paused is not None else False,
            "reason": reason,
            "depth": depth,
            "pending": pending,
            "visibility_timeout": self._visibility_timeout,
        }

    async def pause(self, reason: Optional[str] = None) -> None:
        await self.initialize()
        await self._redis.set(self._paused_key, 1)
        await self._redis.set(self._pause_reason_key, reason or "Paused via API")
        self._logger.warning("Job queue paused: %s", reason or "Paused via API")

    async def resume(self) -> None:
        await self.initialize()
        await self._redis.delete(self._paused_key)
        await self._redis.delete(self._pause_reason_key)
        self._logger.info("Job queue resumed")

    async def update_job(self, job_id: str, update: JobStatusUpdate) -> Job:
        await self.initialize()
        data = await self._redis.hgetall(self._job_key(job_id))
        if not data:
            raise KeyError(job_id)
        job = self._decode_job(data)
        job.status = update.status
        if update.progress is not None:
            job.progress = update.progress
        if update.message:
            job.message = update.message
        if update.pipeline:
            job.pipeline = update.pipeline
            if isinstance(update.pipeline, dict):
                job.attempt = int(update.pipeline.get("attempt", job.attempt) or job.attempt)
                job.max_attempts = int(
                    update.pipeline.get("max_attempts", job.max_attempts) or job.max_attempts
                )
        job.updated_at = datetime.utcnow()
        await self._redis.hset(self._job_key(job_id), mapping=self._encode_job(job))
        await self._redis.zadd(self._job_index, {job_id: job.updated_at.timestamp()})
        self._logger.debug(
            "Job %s updated: status=%s progress=%s message=%s",
            job_id[:8],
            job.status,
            job.progress,
            job.message,
        )
        return job

    async def schedule_retry(
        self, job: Job, pipeline: Dict[str, Any], message: Optional[str] = None
    ) -> Job:
        await self.initialize()
        next_attempt = job.attempt + 1
        if pipeline:
            pipeline = dict(pipeline)
            pipeline["attempt"] = next_attempt
            pipeline["max_attempts"] = job.max_attempts
        else:
            pipeline = pipeline_for_attempt(next_attempt, max_attempts=job.max_attempts)
        job.attempt = next_attempt
        job.status = JobStatus.PENDING
        job.progress = 0
        job.message = message
        job.pipeline = pipeline
        job.updated_at = datetime.utcnow()
        if job.encoding is None:
            job.encoding = {}
        job.encoding["pipeline"] = pipeline
        payload = self._encode_job(job)
        await self._redis.hset(self._job_key(job.id), mapping=payload)
        await self._redis.zadd(self._job_index, {job.id: datetime.utcnow().timestamp()})
        await self._redis.xadd(
            self._stream, {"payload": json.dumps(payload)}, maxlen=10_000, approximate=True
        )
        await self._redis.sadd(self._path_index, job.path)
        await self._redis.set(self._path_key(job.path), job.id)
        self._logger.info(
            "Retrying job %s (attempt %s/%s) with pipeline %s",
            job.id[:8],
            job.attempt,
            job.max_attempts,
            pipeline,
        )
        return job

    async def acknowledge(self, message_id: str, job_id: str) -> None:
        await self.initialize()
        try:
            await self._redis.xack(self._stream, self._group, message_id)
            await self._redis.xdel(self._stream, message_id)
        finally:
            data = await self._redis.hgetall(self._job_key(job_id))
            if data:
                job = self._decode_job(data)
                if job.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
                    await self._redis.srem(self._path_index, job.path)
                    await self._redis.delete(self._path_key(job.path))

    async def clear_processed(self) -> int:
        await self.initialize()
        job_ids = await self._redis.zrange(self._job_index, 0, -1)
        removed = 0
        for job_id in job_ids:
            data = await self._redis.hgetall(self._job_key(job_id))
            if not data:
                await self._redis.zrem(self._job_index, job_id)
                continue
            status = data.get("status")
            if status in {JobStatus.COMPLETED, JobStatus.FAILED}:
                await self._redis.delete(self._job_key(job_id))
                await self._redis.zrem(self._job_index, job_id)
                removed += 1
        return removed

    async def scan_directory(
        self,
        library: str,
        root: str,
        profile: str,
        *,
        profile_id: Optional[int] = None,
        encoding: Optional[Dict[str, Any]] = None,
    ) -> List[Job]:
        root_path = Path(self._canonical_path(root))
        if not root_path.exists():
            return []
        jobs_added: List[Job] = []
        entries = list(root_path.rglob("*.*"))
        for entry in entries:
            canonical_entry = Path(self._canonical_path(entry))
            if canonical_entry.suffix.lower() not in self._video_extensions:
                continue
            if "-chromecast" in canonical_entry.stem.lower():
                continue
            if self._already_converted(canonical_entry):
                continue
            job = await self.add_job(
                str(canonical_entry),
                library,
                profile,
                profile_id=profile_id,
                encoding=encoding,
            )
            jobs_added.append(job)
        self._logger.info(
            "Scan complete for %s: %s jobs queued (root=%s)",
            library,
            len(jobs_added),
            root_path,
        )
        return jobs_added
