import asyncio
import json
import logging
import os
import platform
import shlex
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

logging.addLevelName(logging.DEBUG, "VERBOSE")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL == "VERBOSE":
    LOG_LEVEL = "DEBUG"
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:9000")
STREAM_READER_LIMIT = int(os.environ.get("GPU_STREAM_READER_LIMIT", "1000000"))


class OrchestratorLogHandler(logging.Handler):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._client = httpx.Client(base_url=base_url, timeout=5.0)

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        payload = {
            "entries": [
                {
                    "timestamp": datetime.fromtimestamp(
                        record.created, tz=timezone.utc
                    ).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                }
            ]
        }
        try:
            self._client.post("/api/logs/ingest", json=payload)
        except Exception:  # noqa: BLE001
            # Remote logging failures should not block worker progress.
            return


def configure_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.getLevelName(LOG_LEVEL),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger = logging.getLogger("gpu-ffmpeg.worker")
    handler = OrchestratorLogHandler(ORCHESTRATOR_URL)
    handler.setLevel(logging.getLevelName(LOG_LEVEL))
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    return logger


LOGGER = configure_logging()

POLL_INTERVAL = int(os.environ.get("GPU_POLL_INTERVAL", "5"))
# Keep scaling on the GPU to avoid format mismatches between CUDA surfaces and
# software filters.
SCALING_EXPRESSION = "scale_cuda=-2:720:force_original_aspect_ratio=decrease"


def _detect_host_environment() -> dict[str, bool]:
    uname = platform.uname()
    release = uname.release.lower()
    version = uname.version.lower()
    return {"is_wsl": "microsoft" in release or "microsoft" in version}


def _probe_nvenc_capabilities() -> dict[str, bool]:
    capabilities = {"rc_vbr_hq": True, "multipass_fullres": True}
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "quiet",
                "-h",
                "encoder=h264_nvenc",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.SubprocessError as exc:
        LOGGER.warning("Unable to probe NVENC encoder capabilities: %s", exc)
        return capabilities

    output = result.stdout.lower()
    capabilities["rc_vbr_hq"] = "vbr_hq" in output
    capabilities["multipass_fullres"] = "fullres" in output

    LOGGER.info(
        "NVENC capabilities detected (vbr_hq=%s, multipass_fullres=%s)",
        capabilities["rc_vbr_hq"],
        capabilities["multipass_fullres"],
    )
    return capabilities


HOST_ENVIRONMENT = _detect_host_environment()
NVENC_CAPABILITIES = _probe_nvenc_capabilities()

if HOST_ENVIRONMENT["is_wsl"]:
    LOGGER.warning("Detected WSL kernel; NVENC rate-control and multipass support may be limited")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/settings.yaml"))
try:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        _CONFIG = yaml.safe_load(fh) or {}
except FileNotFoundError:
    _CONFIG = {}
PROFILES = _CONFIG.get("profiles", {})
if _CONFIG:
    LOGGER.info(
        "Loaded settings config from %s (%s profiles available)",
        CONFIG_PATH,
        len(PROFILES),
    )
else:
    LOGGER.warning("No settings config present at %s; using defaults", CONFIG_PATH)
FFPROBE_ANALYSIS_CMD = [
    "ffprobe",
    "-v",
    "quiet",
    "-print_format",
    "json",
    "-show_format",
    "-show_streams",
]
OPERATIONAL_CONFIG = _CONFIG.get("operational", {})
REMOVE_ORIGINAL = bool(OPERATIONAL_CONFIG.get("remove_original_after_success", False))


def probe_file(filepath: str | Path) -> dict:
    command = [*FFPROBE_ANALYSIS_CMD, str(filepath)]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.SubprocessError as exc:
        LOGGER.warning("ffprobe analysis failed for %s: %s", filepath, exc)
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        LOGGER.warning("Failed to parse ffprobe output for %s", filepath)
        return {}


def _normalize_language(language: str | None) -> str | None:
    if not language:
        return None
    code = language.lower()
    if code in {"swe", "sv"}:
        return "swe"
    if code in {"eng", "en"}:
        return "eng"
    return code


def _gather_streams(streams: list[dict]) -> tuple[bool, list[dict], list[dict]]:
    video_present = False
    audio_streams: list[dict] = []
    subtitle_streams: list[dict] = []
    audio_pos = 0
    subtitle_pos = 0
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            video_present = True
        elif codec_type == "audio":
            audio_streams.append(
                {
                    "input_index": audio_pos,
                    "language": _normalize_language(stream.get("tags", {}).get("language")),
                    "disposition": stream.get("disposition", {}),
                }
            )
            audio_pos += 1
        elif codec_type == "subtitle":
            subtitle_streams.append(
                {
                    "input_index": subtitle_pos,
                    "language": _normalize_language(stream.get("tags", {}).get("language")),
                    "disposition": stream.get("disposition", {}),
                }
            )
            subtitle_pos += 1
    return video_present, audio_streams, subtitle_streams


def _select_priority_streams(stream_list: list[dict]) -> tuple[list[dict], int | None]:
    mapped: list[dict] = []
    seen_inputs: set[int] = set()

    swedish = [s for s in stream_list if s.get("language") == "swe"]
    english = [s for s in stream_list if s.get("language") == "eng"]

    original = next(
        (s for s in stream_list if s.get("disposition", {}).get("original")),
        None,
    )
    if original is None:
        original = next(
            (s for s in stream_list if s.get("disposition", {}).get("default")),
            None,
        )
    if original is None and stream_list:
        original = stream_list[0]

    for candidate in [*swedish, *english, original]:
        if candidate is None:
            continue
        idx = candidate["input_index"]
        if idx in seen_inputs:
            continue
        mapped.append(candidate)
        seen_inputs.add(idx)

    default_idx: int | None = None
    if swedish:
        for i, stream in enumerate(mapped):
            if stream in swedish:
                default_idx = i
                break
    elif mapped:
        default_idx = 0
    return mapped, default_idx


def _build_disposition_flags(
    mapped_streams: list[dict], default_idx: int | None, stream_type: str
) -> list[str]:
    flags: list[str] = []
    for output_idx in range(len(mapped_streams)):
        disposition_value = "default" if default_idx == output_idx else "0"
        flags.extend([f"-disposition:{stream_type}:{output_idx}", disposition_value])
    return flags


def build_ffmpeg_command(analysis_json: dict, input_path: Path, output_path: Path) -> list[str]:
    profile = analysis_json.get("encoding") or PROFILES.get(
        analysis_json.get("profile"),
        {},
    )

    profile = profile or {}
    maxrate = profile.get("max_bitrate", "8M")
    bufsize = profile.get("bufsize", "16M")
    level = profile.get("level", "4.1")
    max_fps = int(profile.get("max_fps", 30) or 30)
    preset = str(profile.get("preset", "p5"))
    rc_mode = str(profile.get("rc", "vbr_hq")).lower()
    cq = str(profile.get("cq", 18))
    multipass_mode: str | None = None
    if rc_mode == "vbr_hq":
        multipass_mode = "fullres"

    if rc_mode == "vbr_hq" and not NVENC_CAPABILITIES.get("rc_vbr_hq", True):
        LOGGER.warning(
            "Requested rc mode vbr_hq is unavailable; falling back to vbr (WSL=%s)",
            HOST_ENVIRONMENT["is_wsl"],
        )
        rc_mode = "vbr"
        multipass_mode = None

    if multipass_mode and not NVENC_CAPABILITIES.get("multipass_fullres", True):
        LOGGER.warning(
            "NVENC multipass fullres mode is unavailable; continuing without multipass",
        )
        multipass_mode = None
    audio_cfg = profile.get("audio", {})
    audio_codec = audio_cfg.get("codec", "aac")
    audio_bitrate = audio_cfg.get("bitrate", "192k")
    audio_channels = int(audio_cfg.get("channels", 2) or 2)

    streams = analysis_json.get("streams", [])
    video_present, audio_streams, subtitle_streams = _gather_streams(streams)

    selected_audio, default_audio_idx = _select_priority_streams(audio_streams)
    selected_subtitles, default_sub_idx = _select_priority_streams(subtitle_streams)

    command: list[str] = [
        "ffmpeg",
        "-y",
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
        "-i",
        str(input_path),
    ]

    if video_present:
        command.extend(["-map", "0:v"])

    for audio_stream in selected_audio:
        command.extend(["-map", f"0:a:{audio_stream['input_index']}"])

    for subtitle_stream in selected_subtitles:
        command.extend(["-map", f"0:s:{subtitle_stream['input_index']}"])

    audio_dispositions = _build_disposition_flags(selected_audio, default_audio_idx, "a")
    subtitle_dispositions = _build_disposition_flags(selected_subtitles, default_sub_idx, "s")

    filters = [SCALING_EXPRESSION]
    if max_fps > 0:
        filters.append(f"fps={min(max_fps, 30)}")
    video_filter = ",".join(filters)

    command.extend(
        [
            "-vf",
            video_filter,
            "-c:v",
            "h264_nvenc",
            "-rc",
            rc_mode,
            "-preset",
            preset,
            "-profile:v",
            "high",
            "-level",
            level,
            "-cq",
            cq,
            "-maxrate",
            maxrate,
            "-bufsize",
            bufsize,
            "-movflags",
            "+faststart",
        ]
    )

    if multipass_mode:
        command.extend(["-multipass", multipass_mode])

    if selected_audio:
        command.extend(
            [
                "-c:a",
                audio_codec,
                "-b:a",
                audio_bitrate,
                "-ac",
                str(audio_channels),
            ]
        )

    command.extend(audio_dispositions)
    command.extend(subtitle_dispositions)

    command.extend(["-progress", "pipe:1", str(output_path)])

    return command


def _loggable_command(command: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in command)


def run_conversion(command: list[str], progress_callback) -> tuple[int, list[str]]:
    LOGGER.info("Starting FFmpeg with command: %s", _loggable_command(command))
    ffmpeg_logs: deque[str] = deque(maxlen=100)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    try:
        while True:
            line = process.stdout.readline(STREAM_READER_LIMIT)
            if line == "" and process.poll() is not None:
                break
            if not line:
                continue
            text_line = line.strip()
            if text_line.startswith("out_time_ms="):
                try:
                    out_time_ms = int(text_line.split("=", 1)[1])
                except ValueError:
                    continue
                progress_callback(out_time_ms)
            else:
                LOGGER.debug("ffmpeg: %s", text_line)
                ffmpeg_logs.append(text_line)
    finally:
        return_code = process.wait()
    return return_code, list(ffmpeg_logs)


def _extract_duration(analysis: dict) -> float:
    try:
        return float(analysis.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        return 0.0


def _progress_callback_factory(
    duration: float,
    loop: asyncio.AbstractEventLoop,
    client: httpx.AsyncClient,
    job_id: str,
) -> tuple[callable, dict, dict]:
    last_progress = {"value": 5}
    last_update_ts = {"value": time.monotonic()}

    def progress_callback(out_time_ms: int) -> None:
        if duration <= 0:
            return
        elapsed_seconds = out_time_ms / 1_000_000.0
        percentage = min(99, int((elapsed_seconds / duration) * 100))
        now = time.monotonic()
        if percentage <= last_progress["value"] or now - last_update_ts["value"] < 1:
            return
        last_progress["value"] = percentage
        last_update_ts["value"] = now
        loop.call_soon_threadsafe(
            asyncio.create_task,
            update_job_status(
                client,
                job_id,
                "running",
                percentage,
                f"Encoded {elapsed_seconds:.1f}s",
            ),
        )

    return progress_callback, last_progress, last_update_ts


async def claim_job(client: httpx.AsyncClient) -> dict | None:
    try:
        response = await client.get("/api/jobs/next")
    except httpx.RequestError as exc:
        LOGGER.error("HTTP error while claiming job: %s", exc)
        return None
    if response.status_code == 409:
        detail = response.json()
        LOGGER.warning("Job queue paused: %s", detail.get("reason") or detail.get("detail"))
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
    try:
        response = await client.post(f"/api/jobs/{job_id}/status", json=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        LOGGER.error("Failed to update job %s status: %s", job_id[:8], exc)


async def _probe_duration(source: Path) -> float:
    analysis = await asyncio.to_thread(probe_file, source)
    try:
        return float(analysis.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        LOGGER.debug("Unable to parse duration from ffprobe output for %s", source)
        return 0.0


async def _validate_output(output: Path, expected_duration: float) -> bool:
    if not output.exists():
        return False
    try:
        stat = output.stat()
    except OSError:
        return False
    if stat.st_size <= 0:
        return False
    if expected_duration > 0:
        output_duration = await _probe_duration(output)
        if output_duration <= 0:
            return False
        if abs(output_duration - expected_duration) > 1.0:
            LOGGER.warning(
                "Output duration for %s mismatches source (%.2fs vs %.2fs)",
                output,
                output_duration,
                expected_duration,
            )
            return False
    return True


def _build_output_path(source: Path) -> Path:
    return source.parent / f"{source.stem}-chromecast.mp4"


async def _maybe_remove_original(source: Path, output_path: Path, expected_duration: float) -> bool:
    if not REMOVE_ORIGINAL:
        return False
    if not await _validate_output(output_path, expected_duration):
        LOGGER.warning("Keeping original %s because output did not validate", source)
        return False
    try:
        source.unlink()
    except OSError as exc:
        LOGGER.warning("Failed to remove original %s: %s", source, exc)
        return False
    LOGGER.info("Removed original %s after verifying output at %s", source, output_path)
    return True


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

    analysis = await asyncio.to_thread(probe_file, playback_target)
    analysis = analysis or {}
    duration = _extract_duration(analysis)
    if duration == 0:
        LOGGER.warning("Duration probe for %s returned 0 seconds", playback_target)

    output_path = _build_output_path(playback_target)
    encoding = job.get("encoding") or PROFILES.get(job["profile"], {})
    if not encoding:
        LOGGER.warning(
            "No encoding settings supplied for profile %s; using defaults.",
            job["profile"],
        )

    analysis["encoding"] = encoding
    analysis["profile"] = job.get("profile")

    if await _validate_output(output_path, duration):
        message = f"Output already present at {output_path}; skipping encode"
        await update_job_status(client, job_id, "completed", 100, message)
        LOGGER.info("Job %s completed from existing output %s", job_id[:8], output_path)
        if await _maybe_remove_original(playback_target, output_path, duration):
            await update_job_status(
                client,
                job_id,
                "completed",
                100,
                f"{message}. Original removed",
            )
        return

    command = build_ffmpeg_command(analysis, playback_target, output_path)

    loop = asyncio.get_running_loop()
    progress_callback, _, _ = _progress_callback_factory(duration, loop, client, job_id)

    return_code, ffmpeg_logs = await asyncio.to_thread(run_conversion, command, progress_callback)

    if return_code == 0:
        message = f"Encoding finished to {output_path}"
        if not await _validate_output(output_path, duration):
            await update_job_status(
                client,
                job_id,
                "failed",
                0,
                f"Encoding finished but output missing or invalid at {output_path}",
            )
            return
        removed = await _maybe_remove_original(playback_target, output_path, duration)
        if removed:
            message = f"{message} (original removed)"
        await update_job_status(client, job_id, "completed", 100, message)
        LOGGER.info("Job %s completed, output: %s", job_id[:8], output_path)
    else:
        message = f"FFmpeg exited with code {return_code}"
        if ffmpeg_logs:
            LOGGER.error(
                "Job %s failed (code %s). Last FFmpeg output:\n%s",
                job_id[:8],
                return_code,
                "\n".join(ffmpeg_logs),
            )
            message = f"{message}; last log line: {ffmpeg_logs[-1]}"
        await update_job_status(client, job_id, "failed", 0, message)


async def main() -> None:
    LOGGER.info(
        "GPU worker starting; polling %s every %ss (log level %s)",
        ORCHESTRATOR_URL,
        POLL_INTERVAL,
        LOG_LEVEL,
    )
    async with httpx.AsyncClient(base_url=ORCHESTRATOR_URL, timeout=30.0) as client:
        while True:
            job = await claim_job(client)
            if not job:
                LOGGER.debug("No job available; sleeping for %ss", POLL_INTERVAL)
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
