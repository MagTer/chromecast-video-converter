from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import List, Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.orm import Session, sessionmaker

from .db import Base

LOGGER = logging.getLogger("orchestrator.profiles")


class EncodingProfile(Base):
    __tablename__ = "encoding_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    codec = Column(String, nullable=False)
    profile_tier = Column(String, nullable=False, default="high")
    max_resolution = Column(String, nullable=False)
    bitrate = Column(String, nullable=False, default="8M")
    max_bitrate = Column(String, nullable=False)
    bufsize = Column(String, nullable=False)
    preset = Column(String, nullable=False)
    cq = Column(Integer, nullable=False, default=18)
    rc = Column(String, nullable=False, default="vbr_hq")
    level = Column(String, nullable=False, default="4.1")
    max_fps = Column(Integer, nullable=False, default=30)
    bframes = Column(Integer, nullable=False, default=2)
    lookahead = Column(Integer, nullable=False, default=24)
    adaptive_b_frames = Column(Boolean, nullable=False, default=True)
    aq = Column(Boolean, nullable=False, default=True)
    spatial_aq = Column(Boolean, nullable=False, default=True)
    temporal_aq = Column(Boolean, nullable=False, default=True)
    audio_codec = Column(String, nullable=False, default="aac")
    audio_bitrate = Column(String, nullable=False, default="192k")
    audio_channels = Column(Integer, nullable=False, default=2)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "codec": self.codec,
            "profile": self.profile_tier,
            "max_resolution": self.max_resolution,
            "bitrate": self.bitrate,
            "max_bitrate": self.max_bitrate,
            "bufsize": self.bufsize,
            "preset": self.preset,
            "cq": self.cq,
            "rc": self.rc,
            "level": self.level,
            "max_fps": self.max_fps,
            "bframes": self.bframes,
            "lookahead": self.lookahead,
            "adaptive_b_frames": bool(self.adaptive_b_frames),
            "aq": bool(self.aq),
            "spatial_aq": bool(self.spatial_aq),
            "temporal_aq": bool(self.temporal_aq),
            "audio": {
                "codec": self.audio_codec,
                "bitrate": self.audio_bitrate,
                "channels": self.audio_channels,
            },
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LibraryConfig(Base):
    __tablename__ = "libraries"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    root = Column(String, nullable=False)
    depth = Column(String, nullable=False, default="max")
    profile_id = Column(Integer, ForeignKey("encoding_profiles.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "root": self.root,
            "depth": self.depth,
            "profile_id": self.profile_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ProfileData:
    name: str
    codec: str
    profile_tier: str
    max_resolution: str
    bitrate: str
    max_bitrate: str
    bufsize: str
    preset: str
    cq: int
    rc: str
    level: str
    max_fps: int
    bframes: int
    lookahead: int
    adaptive_b_frames: bool
    aq: bool
    spatial_aq: bool
    temporal_aq: bool
    audio_codec: str
    audio_bitrate: str
    audio_channels: int


@dataclass
class LibraryData:
    name: str
    root: str
    depth: str
    profile_id: int


class ProfileStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory
        self._lock = RLock()

    def _session(self) -> Session:
        return self._session_factory()

    def list_profiles(self) -> List[EncodingProfile]:
        stmt = select(EncodingProfile).order_by(EncodingProfile.name.asc())
        with self._lock, self._session() as session:
            return list(session.scalars(stmt).all())

    def get(self, profile_id: int) -> Optional[EncodingProfile]:
        with self._lock, self._session() as session:
            return session.get(EncodingProfile, profile_id)

    def get_by_name(self, name: str) -> Optional[EncodingProfile]:
        stmt = select(EncodingProfile).where(EncodingProfile.name == name)
        with self._lock, self._session() as session:
            return session.scalar(stmt)

    def create(self, data: ProfileData) -> EncodingProfile:
        with self._lock, self._session() as session:
            now = datetime.utcnow()
            profile = EncodingProfile(
                name=data.name,
                codec=data.codec,
                profile_tier=data.profile_tier,
                max_resolution=data.max_resolution,
                bitrate=data.bitrate,
                max_bitrate=data.max_bitrate,
                bufsize=data.bufsize,
                preset=data.preset,
                cq=data.cq,
                rc=data.rc,
                level=data.level,
                max_fps=data.max_fps,
                bframes=data.bframes,
                lookahead=data.lookahead,
                adaptive_b_frames=data.adaptive_b_frames,
                aq=data.aq,
                spatial_aq=data.spatial_aq,
                temporal_aq=data.temporal_aq,
                audio_codec=data.audio_codec,
                audio_bitrate=data.audio_bitrate,
                audio_channels=data.audio_channels,
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            LOGGER.info("Created encoding profile %s (#%s)", profile.name, profile.id)
            return profile

    def update(self, profile_id: int, data: ProfileData) -> EncodingProfile:
        with self._lock, self._session() as session:
            profile = session.get(EncodingProfile, profile_id)
            if profile is None:
                raise KeyError(profile_id)
            for key, value in data.__dict__.items():
                setattr(profile, key, value)
            profile.updated_at = datetime.utcnow()
            session.add(profile)
            session.commit()
            session.refresh(profile)
            LOGGER.info("Updated encoding profile %s (#%s)", profile.name, profile.id)
            return profile

    def delete(self, profile_id: int) -> None:
        with self._lock, self._session() as session:
            profile = session.get(EncodingProfile, profile_id)
            if profile is None:
                raise KeyError(profile_id)

            libraries = session.scalars(
                select(LibraryConfig.name).where(LibraryConfig.profile_id == profile_id)
            ).all()
            if libraries:
                joined = ", ".join(libraries)
                raise ValueError(f"Profile in use by libraries: {joined}")

            from .library_entries import LibraryEntry  # Imported lazily to avoid cycle

            linked_entry = session.scalar(
                select(LibraryEntry.id).where(LibraryEntry.profile_id == profile_id).limit(1)
            )
            if linked_entry is not None:
                raise ValueError("Profile in use by library entries")

            session.delete(profile)
            session.commit()

    def upsert(self, data: ProfileData) -> EncodingProfile:
        existing = self.get_by_name(data.name)
        if existing:
            return self.update(existing.id, data)
        return self.create(data)


class LibraryConfigStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory
        self._lock = RLock()

    def _session(self) -> Session:
        return self._session_factory()

    def list_libraries(self) -> List[LibraryConfig]:
        stmt = select(LibraryConfig).order_by(LibraryConfig.name.asc())
        with self._lock, self._session() as session:
            return list(session.scalars(stmt).all())

    def get(self, name: str) -> Optional[LibraryConfig]:
        stmt = select(LibraryConfig).where(LibraryConfig.name == name)
        with self._lock, self._session() as session:
            return session.scalar(stmt)

    def upsert(self, data: LibraryData) -> LibraryConfig:
        with self._lock, self._session() as session:
            existing = session.scalar(select(LibraryConfig).where(LibraryConfig.name == data.name))
            now = datetime.utcnow()
            if existing:
                existing.root = data.root
                existing.depth = data.depth
                existing.profile_id = data.profile_id
                existing.updated_at = now
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            library = LibraryConfig(
                name=data.name,
                root=data.root,
                depth=data.depth,
                profile_id=data.profile_id,
                created_at=now,
                updated_at=now,
            )
            session.add(library)
            session.commit()
            session.refresh(library)
            return library

    def update_profile(self, name: str, profile_id: int) -> LibraryConfig:
        with self._lock, self._session() as session:
            library = session.scalar(select(LibraryConfig).where(LibraryConfig.name == name))
            if library is None:
                raise KeyError(name)
            library.profile_id = profile_id
            library.updated_at = datetime.utcnow()
            session.add(library)
            session.commit()
            session.refresh(library)
            return library

    def delete(self, name: str) -> None:
        with self._lock, self._session() as session:
            library = session.scalar(select(LibraryConfig).where(LibraryConfig.name == name))
            if library is None:
                raise KeyError(name)
            session.delete(library)
            session.commit()
