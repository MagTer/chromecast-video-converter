from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text, select
from sqlalchemy.orm import sessionmaker

from .db import Base


class JobHistoryStatus(str):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobHistory(Base):
    __tablename__ = "job_history"

    id = Column(Integer, primary_key=True)
    job_id = Column(String(64), unique=True, nullable=False)
    path = Column(Text, nullable=False)
    library = Column(String(100), nullable=False)
    profile = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


@dataclass
class JobHistoryEntry:
    job_id: str
    path: str
    library: str
    profile: str
    status: str
    message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class JobHistoryStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._Session = session_factory

    def _session(self):
        return self._Session()

    def record(self, entry: JobHistoryEntry) -> JobHistory:
        with self._session() as session:
            existing = session.scalar(select(JobHistory).where(JobHistory.job_id == entry.job_id))
            timestamp = datetime.utcnow()
            if existing:
                existing.status = entry.status
                existing.message = entry.message
                existing.completed_at = entry.completed_at
                existing.started_at = existing.started_at or entry.started_at or timestamp
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            record = JobHistory(
                job_id=entry.job_id,
                path=entry.path,
                library=entry.library,
                profile=entry.profile,
                status=entry.status,
                message=entry.message,
                started_at=entry.started_at or timestamp,
                completed_at=entry.completed_at,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_recent(self, limit: int = 100) -> List[JobHistoryEntry]:
        with self._session() as session:
            records = session.scalars(
                select(JobHistory)
                .where(JobHistory.status.in_([JobHistoryStatus.COMPLETED, JobHistoryStatus.FAILED]))
                .order_by(JobHistory.started_at.desc())
                .limit(limit)
            ).all()
        return [
            JobHistoryEntry(
                job_id=record.job_id,
                path=record.path,
                library=record.library,
                profile=record.profile,
                status=record.status,
                message=record.message,
                started_at=record.started_at,
                completed_at=record.completed_at,
            )
            for record in records
        ]

    def as_dicts(self, limit: int = 100) -> List[dict]:
        return [asdict(entry) for entry in self.list_recent(limit=limit)]
