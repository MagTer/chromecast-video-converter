from __future__ import annotations

import logging
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from flask import current_app

from .database import db
from .models import MediaAsset, MediaStatus

LOGGER = logging.getLogger(__name__)


def spawn_worker(app: Any) -> None:
    thread = threading.Thread(target=_worker_loop, args=(app,), daemon=True)
    thread.start()
    LOGGER.info("Worker thread started")


def _worker_loop(app: Any) -> None:
    while True:
        with app.app_context():
            job = (
                MediaAsset.query.filter_by(status=MediaStatus.Queued.value)
                .order_by(MediaAsset.id.asc())
                .first()
            )
            if not job:
                time.sleep(app.config.get("WORKER_POLL_INTERVAL", 2.0))
                continue

            job.status = MediaStatus.Processing.value
            db.session.commit()

            try:
                process_job(job)
            except Exception:  # noqa: BLE001
                job.status = MediaStatus.Failed.value
                job.retry_count += 1
                job.error_log = traceback.format_exc()
                db.session.commit()


def process_job(job: MediaAsset) -> None:
    output_root = Path(current_app.config.get("OUTPUT_ROOT", "/output")).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    source_path = Path(job.file_path)
    output_path = output_root / f"{source_path.stem}_converted{source_path.suffix}"

    LOGGER.info("Starting ffmpeg for %s", source_path)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hwaccel",
            "cuda",
            "-i",
            str(source_path),
            "-c:v",
            "h264_nvenc",
            "-c:a",
            "aac",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    stderr = result.stderr
    if result.returncode == 0 and _validate_output(output_path):
        _mark_completed(job, output_path)
        return

    LOGGER.error("ffmpeg failed for %s", source_path)
    job.status = MediaStatus.Failed.value
    job.retry_count += 1
    job.error_log = stderr or job.error_log
    db.session.commit()


def _validate_output(path: Path) -> bool:
    if not path.exists():
        return False
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _mark_completed(job: MediaAsset, output_path: Path) -> None:
    job.output_path = str(output_path)
    delete_original = current_app.config.get("DELETE_ORIGINAL", False)

    if delete_original:
        Path(job.file_path).unlink(missing_ok=True)
        job.status = MediaStatus.Archived.value
    else:
        job.status = MediaStatus.Completed.value

    job.error_log = None
    db.session.commit()
