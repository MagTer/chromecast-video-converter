import asyncio
import logging
import os
import re
import time
from collections import deque
from pathlib import Path

import httpx
import yaml

logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger("gpu-ffmpeg.worker")

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:9000")
POLL_INTERVAL = int(os.environ.get("GPU_POLL_INTERVAL", "5"))
SCALING_EXPRESSION = "scale=1280:-1"

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/quality.yaml"))
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path("/app/config/quality.sample.yaml")
try:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        _CONFIG = yaml.safe_load(fh) or {}
except FileNotFoundError:
    _CONFIG = {}
PROFILES = _CONFIG.get("profiles", {})
FFPROBE_CMD = [
    "ffprobe",
    "-v",
    "error",
    "-show_entries",
    "format=duration",
    "-of",
    "default=noprint_wrappers=1:nokey=1",
]
PROGRESS_RE = re.compile(r"time=(\d+:\d+:\d+\.\d+)")


async def claim_job(client: httpx.AsyncClient) -> dict | None:
    try:
        response = await client.get("/api/jobs/next")
    except httpx.RequestError as exc:
        LOGGER.error("HTTP error while claiming job: %s", exc)
        return None
    if response.status_code == 204:
        return None
    response.raise_for_status()
    return response.json()


async def update_job_status(
    client: httpx.AsyncClient,
    job_id: str,
    status: str,
    progress: int,
    message: str | None = None,
) -> None:
    payload = {"status": status, "progress": progress}
    if message:
        payload["message"] = message
    response = await client.post(f"/api/jobs/{job_id}/status", json=payload)
    response.raise_for_status()


async def _parse_timecode(code: str) -> float:
    hours, minutes, seconds = code.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


async def _probe_duration(source: Path) -> float:
    command = [*FFPROBE_CMD, str(source)]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0.0
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0


async def _monitor_progress(
    stream: asyncio.StreamReader,
    duration: float,
    client: httpx.AsyncClient,
    job_id: str,
    last_progress: dict[str, int],
) -> None:
    last_time = {"value": 0.0}
    last_update_ts = time.monotonic()
    while True:
        line = await stream.readline()
        if not line:
            break
        text = line.decode(errors="ignore").strip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key not in ("out_time", "out_time_ms", "out_time_us"):
            continue
        try:
            if key == "out_time":
                timecode = await _parse_timecode(value)
            else:
                time_ms = float(value)
                timecode = time_ms / (1000.0 if "ms" in key else 1_000_000.0)
        except ValueError:
            continue
        status_msg = "Encoding in progress"
        if duration > 0:
            progress = min(99, int((timecode / duration) * 100))
        else:
            if timecode - last_time["value"] < 5:
                continue
            progress = min(last_progress["value"] + 1, 2)
            remaining = max(0, 1200 - timecode)
            status_msg = f"Encoded {timecode:.1f}s, ETA {remaining:.0f}s"
        now = time.monotonic()
        if progress > last_progress["value"] and now - last_update_ts >= 1:
            await update_job_status(
                client,
                job_id,
                "running",
                progress,
                status_msg,
            )
            last_progress["value"] = progress
            last_update_ts = now
        last_time["value"] = timecode


def _build_output_path(source: Path) -> Path:
    return source.parent / f"{source.stem}-chromecast.mp4"


def _build_ffmpeg_command(profile_name: str, source: Path, target: Path) -> list[str]:
    profile = PROFILES.get(profile_name, {})
    maxrate = profile.get("max_bitrate", "8M")
    bufsize = profile.get("bufsize", "16M")
    level = profile.get("level", "4.1")
    audio_cfg = profile.get("audio", {})
    audio_codec = audio_cfg.get("codec", "aac")
    audio_bitrate = audio_cfg.get("bitrate", "192k")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        SCALING_EXPRESSION,
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p5",
        "-profile:v",
        "high",
        "-level",
        level,
        "-cq",
        "18",
        "-maxrate",
        maxrate,
        "-bufsize",
        bufsize,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        audio_codec,
        "-b:a",
        audio_bitrate,
        "-progress",
        "pipe:1",
        str(target),
    ]
    return command


async def _run_ffmpeg(
    command: list[str], duration: float, client: httpx.AsyncClient, job_id: str
) -> int:
    LOGGER.debug("Executing ffmpeg command: %s", " ".join(command))
    proc = await asyncio.create_subprocess_exec(
        *command,
        stderr=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    last_progress = {"value": 5}
    recent_lines = deque(maxlen=20)
    progress_task = asyncio.create_task(
        _monitor_progress(proc.stdout, duration, client, job_id, last_progress)
    )

    progress_task = asyncio.create_task(
        _monitor_progress(proc.stdout, duration, client, job_id, last_progress)
    )
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        decoded = line.decode(errors="ignore")
        recent_lines.append(decoded.strip())
    return_code = await proc.wait()
    await progress_task
    if return_code != 0:
        LOGGER.error(
            "FFmpeg job %s failed (code %s). Last stderr lines:\n%s",
            job_id[:8],
            return_code,
            "\n".join(recent_lines),
        )
    return return_code


async def process_job(client: httpx.AsyncClient, job: dict) -> None:
    job_id = job["id"]
    source = job["path"]
    LOGGER.info("Picked up job %s for %s", job_id[:8], source)
    await update_job_status(client, job_id, "running", 5, "Allocated to GPU worker")
    playback_target = Path(source)
    if not playback_target.exists():
        message = f"Source file not found: {source}"
        LOGGER.error("%s", message)
        await update_job_status(client, job_id, "failed", 0, message)
        return
    duration = await _probe_duration(playback_target)
    output_path = _build_output_path(playback_target)
    command = _build_ffmpeg_command(job["profile"], playback_target, output_path)
    return_code = await _run_ffmpeg(command, duration, client, job_id)
    if return_code == 0:
        await update_job_status(
            client, job_id, 100, 100, f"Encoding finished to {output_path}"
        )
        LOGGER.info("Job %s completed, output: %s", job_id[:8], output_path)
    else:
        message = f"FFmpeg exited with code {return_code}"
        await update_job_status(client, job_id, "failed", 0, message)


async def main() -> None:
    async with httpx.AsyncClient(base_url=ORCHESTRATOR_URL, timeout=30.0) as client:
        while True:
            job = await claim_job(client)
            if not job:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            try:
                await process_job(client, job)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Job %s failed: %s", job["id"][:8], exc)
                await update_job_status(client, job["id"], "failed", 0, str(exc))
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
