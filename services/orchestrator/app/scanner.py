from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from flask import current_app

from .database import db
from .models import MediaAsset, MediaStatus

LOGGER = logging.getLogger(__name__)

PARTIAL_HASH_SIZE = 16 * 1024


def partial_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file_handle:
        hasher.update(file_handle.read(PARTIAL_HASH_SIZE))
    return hasher.hexdigest()


def scan() -> int:
    """Synchronize the database with the media files on disk."""

    media_root = Path(current_app.config.get("MEDIA_ROOT", "/watch"))
    if not media_root.exists():
        LOGGER.warning("Media root %s does not exist; skipping scan", media_root)
        return 0

    tracked_assets: Dict[str, MediaAsset] = {
        asset.file_path: asset for asset in MediaAsset.query.all()
    }
    processed = 0

    for path in media_root.rglob("*"):
        if not path.is_file():
            continue
        processed += 1
        _handle_file(path, tracked_assets)

    for missing_asset in tracked_assets.values():
        if missing_asset.status_enum is MediaStatus.Archived:
            continue
        LOGGER.info("Removing missing asset %s", missing_asset.file_path)
        db.session.delete(missing_asset)

    db.session.commit()
    return processed


def _handle_file(path: Path, tracked_assets: Dict[str, MediaAsset]) -> None:
    stat = path.stat()
    modified_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    file_size = stat.st_size
    normalized_path = str(path.resolve())

    existing = tracked_assets.pop(normalized_path, None)
    if existing:
        if existing.file_size != file_size or existing.modified_time != modified_time:
            LOGGER.info("Detected modification to %s; resetting to New", normalized_path)
            existing.file_size = file_size
            existing.modified_time = modified_time
            existing.file_hash = partial_hash(path)
            existing.status = MediaStatus.New.value
        return

    file_hash = partial_hash(path)
    renamed = MediaAsset.query.filter_by(file_hash=file_hash).first()
    if renamed:
        LOGGER.info("Updating renamed asset %s -> %s", renamed.file_path, normalized_path)
        tracked_assets.pop(renamed.file_path, None)
        renamed.file_path = normalized_path
        renamed.file_size = file_size
        renamed.modified_time = modified_time
        return

    LOGGER.info("Discovered new asset %s", normalized_path)
    new_asset = MediaAsset(
        file_path=normalized_path,
        file_hash=file_hash,
        file_size=file_size,
        modified_time=modified_time,
        status=MediaStatus.New.value,
        retry_count=0,
    )
    db.session.add(new_asset)
