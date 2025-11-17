from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

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
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._video_extensions = {".mp4", ".m4v", ".mov", ".mkv", ".ts", ".flv"}

        self._paused: bool = False
        self._pause_reason: Optional[str] = None

    async def add_job(
        self,
        path: str,
        library: str,
        profile: str,
        encoding: Optional[Dict[str, Any]] = None,
    ) -> Job:
        async with self._lock:
            for job in self._jobs.values():
                if job.path == path and job.status != JobStatus.FAILED:
                    return job
            job = Job(path=path, library=library, profile=profile, encoding=encoding)
            self._jobs[job.id] = job
            return job

    async def list_jobs(self) -> List[Job]:
        async with self._lock:
            return list(self._jobs.values())

    async def acquire_next(self) -> Optional[Job]:
        async with self._lock:
            if self._paused:
                return None
            for job in self._jobs.values():
                if job.status == JobStatus.PENDING:
                    job.status = JobStatus.RUNNING
                    job.updated_at = datetime.utcnow()
                    return job
            return None

    async def queue_state(self) -> Dict[str, object]:
        async with self._lock:
            return {"paused": self._paused, "reason": self._pause_reason}

    async def pause(self, reason: Optional[str] = None) -> None:
        async with self._lock:
            self._paused = True
            self._pause_reason = reason or "Paused via API"

    async def resume(self) -> None:
        async with self._lock:
            self._paused = False
            self._pause_reason = None

    async def update_job(self, job_id: str, update: JobStatusUpdate) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.status = update.status
            if update.progress is not None:
                job.progress = update.progress
            if update.message:
                job.message = update.message
            job.updated_at = datetime.utcnow()
            return job

    async def scan_directory(
        self,
        library: str,
        root: str,
        profile: str,
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
            job = await self.add_job(str(entry), library, profile, encoding=encoding)
            jobs_added.append(job)
        return jobs_added
