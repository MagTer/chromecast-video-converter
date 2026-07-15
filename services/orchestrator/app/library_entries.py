from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, List, Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from .db import Base, create_session_factory
from .utils import resolve_media_path

LOGGER = logging.getLogger("orchestrator.library")


def _canonical_path(value: str | Path) -> str:
    return str(resolve_media_path(value))


class LibraryStatus(str):
    PENDING = "pending"
    CONVERTING = "converting"
    CONVERTED = "converted"
    FAILED = "failed"
    REMOVED = "removed"


class LibraryEntry(Base):
    __tablename__ = "library_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    library: Mapped[str] = mapped_column(String, nullable=False)
    profile: Mapped[str] = mapped_column(String, nullable=False)
    profile_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("encoding_profiles.id"), nullable=True, index=True
    )
    decode_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    scale_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    encode_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=LibraryStatus.PENDING)
    output_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_job_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    original_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    output_compliant: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    compliance_detail: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "library": self.library,
            "profile": self.profile,
            "profile_id": self.profile_id,
            "decode_type": self.decode_type,
            "scale_type": self.scale_type,
            "encode_type": self.encode_type,
            "status": self.status,
            "output_path": self.output_path,
            "last_error": self.last_error,
            "last_job_id": self.last_job_id,
            "original_missing": bool(self.original_missing),
            "output_compliant": self.output_compliant,
            "compliance_detail": self.compliance_detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class EntryUpdate:
    path: str
    library: str
    profile: str
    status: str
    profile_id: Optional[int] = None
    decode_type: Optional[str] = None
    scale_type: Optional[str] = None
    encode_type: Optional[str] = None
    output_path: Optional[str] = None
    last_error: Optional[str] = None
    last_job_id: Optional[str] = None
    original_missing: bool = False


class LibraryEntryStore:
    def __init__(
        self,
        db_path: Path,
        *,
        session_factory: Optional[sessionmaker] = None,
        engine=None,
    ) -> None:
        self._lock = RLock()
        if session_factory is None:
            self._Session, self._engine = create_session_factory(db_path)
        else:
            self._Session = session_factory
            self._engine = engine or self._Session.kw.get("bind")  # type: ignore
            if self._engine is None:
                _, self._engine = create_session_factory(db_path)
        self._ensure_schema()
        LOGGER.info("Library entry store initialized at %s", db_path)

    def _ensure_schema(self) -> None:
        """Add columns introduced after the initial schema to existing DBs.

        SQLAlchemy's create_all only creates missing tables, so pre-existing
        installations need lightweight ALTER TABLE migrations here.
        """
        added_columns = (
            ("output_compliant", "BOOLEAN"),
            ("compliance_detail", "VARCHAR"),
        )
        try:
            with self._engine.connect() as conn:
                rows = conn.exec_driver_sql("PRAGMA table_info(library_entries)").fetchall()
                existing = {row[1] for row in rows}
                if not existing:
                    return  # Table does not exist yet; create_all handles it.
                for column, ddl_type in added_columns:
                    if column not in existing:
                        conn.exec_driver_sql(
                            f"ALTER TABLE library_entries ADD COLUMN {column} {ddl_type}"
                        )
                        LOGGER.info("Added column %s to library_entries", column)
                conn.commit()
        except SQLAlchemyError as exc:  # pragma: no cover - defensive
            LOGGER.warning("Library entry schema migration failed: %s", exc)

    def _session(self) -> Session:
        return self._Session()

    def list_entries(
        self,
        *,
        status: Optional[str] = None,
        library: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[LibraryEntry]:
        stmt = select(LibraryEntry)
        if status:
            stmt = stmt.where(LibraryEntry.status == status)
        if library:
            stmt = stmt.where(LibraryEntry.library == library)
        stmt = stmt.order_by(LibraryEntry.updated_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)
        with self._lock, self._session() as session:
            return list(session.scalars(stmt).all())

    def count_entries(self, *, status: Optional[str] = None, library: Optional[str] = None) -> int:
        stmt = select(func.count()).select_from(LibraryEntry)
        if status:
            stmt = stmt.where(LibraryEntry.status == status)
        if library:
            stmt = stmt.where(LibraryEntry.library == library)
        with self._lock, self._session() as session:
            return int(session.scalar(stmt) or 0)

    def get(self, entry_id: int) -> Optional[LibraryEntry]:
        with self._lock, self._session() as session:
            return session.get(LibraryEntry, entry_id)

    def upsert(self, payload: EntryUpdate) -> LibraryEntry:
        with self._lock, self._session() as session:
            canonical_path = _canonical_path(payload.path)
            existing = session.scalar(
                select(LibraryEntry).where(LibraryEntry.path == canonical_path)
            )
            timestamp = datetime.now(timezone.utc)
            payload_dict = asdict(payload)
            payload_dict["path"] = canonical_path
            if existing:
                for key, value in payload_dict.items():
                    if key == "profile_id" and value is None:
                        continue
                    setattr(existing, key, value)
                existing.updated_at = timestamp
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            entry = LibraryEntry(
                path=canonical_path,
                library=payload.library,
                profile=payload.profile,
                profile_id=payload.profile_id,
                status=payload.status,
                output_path=payload.output_path,
                last_error=payload.last_error,
                last_job_id=payload.last_job_id,
                original_missing=payload.original_missing,
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def attach_job(self, entry_id: int, job_id: str) -> Optional[LibraryEntry]:
        with self._lock, self._session() as session:
            entry = session.get(LibraryEntry, entry_id)
            if entry is None:
                return None
            entry.last_job_id = job_id
            entry.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(entry)
            return entry

    def mark_missing(self, library: str, known_paths: Iterable[str]) -> int:
        known_set = {_canonical_path(path) for path in known_paths}
        stmt = select(LibraryEntry).where(LibraryEntry.library == library)
        if known_set:
            stmt = stmt.where(~LibraryEntry.path.in_(known_set))
        updated = 0
        with self._lock, self._session() as session:
            for entry in session.scalars(stmt).all():
                entry.status = LibraryStatus.REMOVED
                entry.original_missing = True
                entry.updated_at = datetime.now(timezone.utc)
                session.add(entry)
                updated += 1
            session.commit()
        return updated

    def update_status(
        self,
        path: str,
        status: str,
        *,
        library: Optional[str] = None,
        profile: Optional[str] = None,
        profile_id: Optional[int] = None,
        decode_type: Optional[str] = None,
        scale_type: Optional[str] = None,
        encode_type: Optional[str] = None,
        message: Optional[str] = None,
        job_id: Optional[str] = None,
        output_path: Optional[str] = None,
        original_missing: bool = False,
        output_compliant: Optional[bool] = None,
        compliance_detail: Optional[str] = None,
    ) -> LibraryEntry:
        canonical_path = _canonical_path(path)
        with self._lock, self._session() as session:
            entry = session.scalar(select(LibraryEntry).where(LibraryEntry.path == canonical_path))
            timestamp = datetime.now(timezone.utc)
            if entry is None:
                if not library or not profile:
                    raise KeyError("Library and profile are required for new entries")
                entry = LibraryEntry(
                    path=canonical_path,
                    library=library,
                    profile=profile,
                    profile_id=profile_id,
                    status=status,
                    created_at=timestamp,
                )
            entry.status = status
            if profile:
                entry.profile = profile
            if profile_id is not None:
                entry.profile_id = profile_id
            if decode_type is not None:
                entry.decode_type = decode_type
            if scale_type is not None:
                entry.scale_type = scale_type
            if encode_type is not None:
                entry.encode_type = encode_type
            entry.output_path = output_path or entry.output_path
            entry.last_error = message if status == LibraryStatus.FAILED else None
            entry.last_job_id = job_id or entry.last_job_id
            entry.original_missing = original_missing
            if output_compliant is not None:
                entry.output_compliant = output_compliant
                entry.compliance_detail = compliance_detail
            elif status == LibraryStatus.PENDING:
                # A new conversion is queued; the previous verdict no longer applies.
                entry.output_compliant = None
                entry.compliance_detail = None
            entry.updated_at = timestamp
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def safe_update_status(self, *args, **kwargs) -> Optional[LibraryEntry]:
        try:
            return self.update_status(*args, **kwargs)
        except SQLAlchemyError as exc:
            LOGGER.error("Failed to persist library entry update: %s", exc)
            return None

    def update_compliance(
        self, path: str, *, compliant: bool, detail: Optional[str] = None
    ) -> Optional[LibraryEntry]:
        """Persist a compliance verdict without touching the entry status."""
        canonical_path = _canonical_path(path)
        with self._lock, self._session() as session:
            entry = session.scalar(select(LibraryEntry).where(LibraryEntry.path == canonical_path))
            if entry is None:
                LOGGER.warning("No library entry for %s; dropping compliance verdict", path)
                return None
            entry.output_compliant = compliant
            entry.compliance_detail = detail
            entry.updated_at = datetime.now(timezone.utc)
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def delete_original(self, entry: LibraryEntry) -> LibraryEntry:
        source = Path(entry.path)
        if source.exists():
            source.unlink()
        with self._lock, self._session() as session:
            stored = session.get(LibraryEntry, entry.id)
            if stored is None:
                raise KeyError(entry.id)
            stored.status = LibraryStatus.REMOVED
            stored.original_missing = True
            stored.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(stored)
            return stored
