from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import db


class MediaStatus(str, enum.Enum):
    New = "New"
    Queued = "Queued"
    Processing = "Processing"
    Completed = "Completed"
    Failed = "Failed"
    Archived = "Archived"


class MediaAsset(db.Model):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_path: Mapped[str] = mapped_column(unique=True, index=True)
    file_hash: Mapped[str | None] = mapped_column(index=True)
    file_size: Mapped[int]
    modified_time: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(default=MediaStatus.New.value)
    error_log: Mapped[str | None]
    retry_count: Mapped[int] = mapped_column(default=0)
    output_path: Mapped[str | None]

    __table_args__ = (
        CheckConstraint(
            status.in_([state.value for state in MediaStatus]),
            name="valid_media_status",
        ),
    )

    @property
    def status_enum(self) -> MediaStatus:
        return MediaStatus(self.status)
