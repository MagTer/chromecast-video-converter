from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import redis.asyncio as redis
from pydantic import BaseModel, Field


class JobStatus(str):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    path: str
    library: str
    profile: str
    profile_id: Optional[int] = None
    encoding: Optional[Dict[str, Any]] = None
    status: str = JobStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    progress: int = 0
    message: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda value: value.isoformat()}


class JobStatusUpdate(BaseModel):
    status: str
    progress: Optional[int] = None
    message: Optional[str] = None


class JobManager:
    def __init__(self, redis_url: str, *, visibility_timeout: int = 300) -> None:
        self._logger = logging.getLogger(__name__)
        self._video_extensions = {".mp4", ".m4v", ".mov", ".mkv", ".ts", ".flv"}
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._stream = os.environ.get("JOB_QUEUE_STREAM", "job_queue")
        self._group = os.environ.get("JOB_QUEUE_GROUP", "workers")
        self._visibility_timeout = visibility_timeout
        self._paused_key = f"{self._stream}:paused"
        self._pause_reason_key = f"{self._stream}:pause_reason"
        self._path_index = f"{self._stream}:paths"
        self._path_lookup_prefix = f"{self._stream}:path"
        self._job_index = f"{self._stream}:index"
        self._job_data_prefix = f"{self._stream}:job"
        self._depth_key = f"{self._stream}:depth"
        self._ensure_group_lock = asyncio.Lock()

    def _output_path(self, source: Path) -> Path:
        return source.parent / f"{source.stem}-chromecast.mp4"

    def _already_converted(self, source: Path, *, log: bool = True) -> bool:
        output_path = self._output_path(source)
        if not output_path.exists():
            return False
        try:
            output_stat = output_path.stat()
            source_mtime = source.stat().st_mtime
        except OSError:
            return True
        if output_stat.st_size == 0:
            return False
        if output_stat.st_mtime >= source_mtime:
            if log:
                self._logger.info(
                    "Skipping already converted file %s (output: %s)",
                    source,
                    output_path,
                )
            return True
        return False

    @property
    def video_extensions(self) -> set[str]:
        return set(self._video_extensions)

    def output_path(self, source: Path) -> Path:
        return self._output_path(source)

    def is_converted(self, source: Path, *, log: bool = False) -> bool:
        return self._already_converted(source, log=log)

    async def initialize(self) -> None:
        async with self._ensure_group_lock:
            try:
                await self._redis.xgroup_create(self._stream, self._group, id="0-0", mkstream=True)
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    def _job_key(self, job_id: str) -> str:
        return f"{self._job_data_prefix}:{job_id}"

    def _path_key(self, path: str) -> str:
        return f"{self._path_lookup_prefix}:{path}"

    def _encode_job(self, job: Job) -> dict[str, str]:
        payload = job.model_dump()
        payload["created_at"] = payload["created_at"].isoformat()
        payload["updated_at"] = payload["updated_at"].isoformat()
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
            if key in {"created_at", "updated_at"}:
                parsed[key] = datetime.fromisoformat(value)
            elif key == "progress":
                parsed[key] = int(value) if value else 0
            elif key == "profile_id":
                parsed[key] = int(value) if value else None
            elif key == "encoding":
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
        job.status = JobStatus.RUNNING
        job.updated_at = datetime.utcnow()
        await self._redis.hset(self._job_key(job.id), mapping=self._encode_job(job))
        await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})
        self._logger.warning(
            "Re-claimed stalled job %s (path=%s) for consumer %s",
            job.id[:8],
            job.path,
            consumer,
        )
        return message_id, job

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
        source = Path(path)
        if source.suffix.lower() not in self._video_extensions:
            raise ValueError("Unsupported media extension")
        if "-chromecast" in source.stem.lower():
            raise ValueError("Converted outputs are ignored")
        if self._already_converted(source, log=emit_log):
            raise ValueError(f"Output already exists for {path}")

        existing_job_id = await self._redis.get(self._path_key(path))
        if existing_job_id and not force:
            data = await self._redis.hgetall(self._job_key(existing_job_id))
            if data:
                self._logger.debug("Job already tracked for %s", path)
                return self._decode_job(data)

        job = Job(
            path=path,
            library=library,
            profile=profile,
            profile_id=profile_id,
            encoding=encoding,
        )
        encoded = self._encode_job(job)
        await self._redis.hset(self._job_key(job.id), mapping=encoded)
        await self._redis.zadd(self._job_index, {job.id: job.updated_at.timestamp()})
        await self._redis.sadd(self._path_index, path)
        await self._redis.set(self._path_key(path), job.id)
        await self._redis.xadd(
            self._stream, {"payload": json.dumps(encoded)}, maxlen=10_000, approximate=True
        )
        await self._redis.incr(self._depth_key)
        if emit_log:
            self._logger.info(
                "Queued job %s for %s (library=%s, profile=%s)",
                job.id[:8],
                path,
                library,
                profile,
            )
        return job

    async def list_jobs(self, limit: int = 200) -> List[Job]:
        await self.initialize()
        job_ids = await self._redis.zrevrange(self._job_index, 0, limit - 1)
        jobs: List[Job] = []
        for job_id in job_ids:
            data = await self._redis.hgetall(self._job_key(job_id))
            if data:
                jobs.append(self._decode_job(data))
        return jobs

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
        depth = int(await self._redis.get(self._depth_key) or 0)
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
        job.updated_at = datetime.utcnow()
        await self._redis.hset(self._job_key(job_id), mapping=self._encode_job(job))
        await self._redis.zadd(self._job_index, {job_id: job.updated_at.timestamp()})
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
            await self._redis.srem(self._path_index, job.path)
            await self._redis.delete(self._path_key(job.path))
        self._logger.debug(
            "Job %s updated: status=%s progress=%s message=%s",
            job_id[:8],
            job.status,
            job.progress,
            job.message,
        )
        return job

    async def acknowledge(self, message_id: str, job_id: str) -> None:
        await self.initialize()
        try:
            await self._redis.xack(self._stream, self._group, message_id)
            await self._redis.decr(self._depth_key)
        finally:
            data = await self._redis.hgetall(self._job_key(job_id))
            if data:
                job = self._decode_job(data)
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
        root_path = Path(root)
        if not root_path.exists():
            return []
        jobs_added: List[Job] = []
        entries = list(root_path.rglob("*.*"))
        for entry in entries:
            if entry.suffix.lower() not in self._video_extensions:
                continue
            if "-chromecast" in entry.stem.lower():
                continue
            if self._already_converted(entry):
                continue
            job = await self.add_job(
                str(entry),
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
