import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from asyncio.subprocess import PIPE, STDOUT, Process
from collections import deque
from contextlib import suppress
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .capabilities import FfmpegCapabilities
from .ffmpeg_builder import DEFAULT_LANGUAGE_PREFERENCES, FFmpegBuilder
from .utils import detect_host_environment, resolve_media_path

logging.addLevelName(logging.DEBUG, "VERBOSE")

_current_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL == "VERBOSE":
    LOG_LEVEL = "DEBUG"
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:9000")
JOB_VISIBILITY_TIMEOUT = int(os.environ.get("JOB_VISIBILITY_TIMEOUT", "300"))
STREAM_READER_LIMIT = int(os.environ.get("GPU_STREAM_READER_LIMIT", "1000000"))
WORKER_ID = os.environ.get("WORKER_ID", f"worker-{os.getpid()}")
FFPROBE_TIMEOUT = max(1, int(os.environ.get("GPU_FFPROBE_TIMEOUT", "120")))
_ffmpeg_timeout_raw = int(os.environ.get("GPU_FFMPEG_TIMEOUT", "7200"))
FFMPEG_TIMEOUT = _ffmpeg_timeout_raw if _ffmpeg_timeout_raw > 0 else None
_ffmpeg_idle_timeout_raw = int(os.environ.get("GPU_FFMPEG_IDLE_TIMEOUT", "600"))
FFMPEG_IDLE_TIMEOUT = _ffmpeg_idle_timeout_raw if _ffmpeg_idle_timeout_raw > 0 else None
_subtitle_timeout_raw = int(os.environ.get("GPU_SUBTITLE_TIMEOUT", "180"))
SUBTITLE_EXTRACTION_TIMEOUT = _subtitle_timeout_raw if _subtitle_timeout_raw > 0 else None


def _normalize_level(level: str) -> str:
    normalized = level.upper()
    if normalized == "DEBUG":
        return "VERBOSE"
    return normalized


def _derive_source_category(logger_name: str) -> tuple[str, str]:
    normalized = logger_name or "gpu-ffmpeg"
    parts = normalized.split(".")
    source = parts[0] if parts else normalized
    category = ".".join(parts[1:]) if len(parts) > 1 else normalized
    return source, category or source


class OrchestratorLogHandler(logging.Handler):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._client = httpx.Client(base_url=base_url, timeout=5.0)

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        severity = _normalize_level(record.levelname)
        source, category = _derive_source_category(record.name)
        payload = {
            "entries": [
                {
                    "timestamp": datetime.fromtimestamp(
                        record.created, tz=timezone.utc
                    ).isoformat(),
                    "level": record.levelname,
                    "severity": severity,
                    "source": source,
                    "category": category,
                    "logger": record.name,
                    "message": message,
                    "request_id": _current_request_id.get(),
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

SUPPORTED_TEXT_SUBTITLE_CODECS = {
    "subrip",
    "ass",
    "ssa",
    "webvtt",
    "mov_text",
    "stl",
    "srt",
}

EXPORT_SUBTITLE_LANGUAGES = {"en", "eng", "sv", "swe"}


def _sanitize_language_tag(language: str | None) -> str:
    if not language:
        return "und"
    cleaned = "".join(ch for ch in language.lower() if ch.isalnum())
    return cleaned[:8] or "und"


def _subtitle_sidecar_path(output_path: Path, stream_index: int, language: str | None) -> Path:
    name = output_path.stem
    lang_tag = _sanitize_language_tag(language)
    return output_path.with_name(f"{name}.{lang_tag}.sub{stream_index}.srt")


def _is_text_subtitle(stream: dict) -> bool:
    codec = (stream.get("codec_name") or "").lower()
    return codec in SUPPORTED_TEXT_SUBTITLE_CODECS


HOST_ENVIRONMENT = detect_host_environment()
FFMPEG_CAPABILITIES = FfmpegCapabilities()

PROFILES: dict = {}
OPERATIONAL_CONFIG: dict = {}
REMOVE_ORIGINAL = False
GPU_TELEMETRY_INTERVAL = int(os.environ.get("GPU_TELEMETRY_INTERVAL", "30"))
LANGUAGE_PREFERENCES: tuple[str, ...] = DEFAULT_LANGUAGE_PREFERENCES
BUILTIN_PROFILE = {
    "name": "chromecast",
    "gpu": {
        "mode": "gpu",
        "codec": "h264",
        "profile": "high",
        "level": "4.1",
        "resolution": "1920x1080",
        "max_fps": 30,
        "bitrate": "5M",
        "max_bitrate": "10M",
        "bufsize": "16M",
        "preset": "p7",
        "rc": "vbr",
        "cq": 18,
        "bframes": 2,
        "lookahead": 24,
        "adaptive_b_frames": True,
        "aq": True,
        "spatial_aq": True,
        "temporal_aq": True,
        "aq_strength": 7,
        "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
    },
    "cpu": {
        "mode": "cpu",
        "codec": "h264",
        "profile": "high",
        "level": "4.1",
        "resolution": "1920x1080",
        "max_fps": 30,
        "bitrate": "5M",
        "max_bitrate": "8M",
        "bufsize": "16M",
        "preset": "slow",
        "rc": "crf",
        "cq": 20,
        "bframes": 2,
        "lookahead": 0,
        "adaptive_b_frames": True,
        "aq": False,
        "spatial_aq": False,
        "temporal_aq": False,
        "aq_strength": 7,
        "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
    },
    "pipeline": {
        "decode_type": "gpu",
        "scale_type": "gpu",
        "encode_type": "gpu",
        "attempt": 1,
        "max_attempts": 5,
    },
}


def _hydrate_config(raw: dict) -> None:
    global PROFILES, OPERATIONAL_CONFIG, REMOVE_ORIGINAL, LANGUAGE_PREFERENCES
    profiles_raw = raw.get("profiles", {}) or {}
    if isinstance(profiles_raw, list):
        PROFILES = {item.get("name"): item for item in profiles_raw if item.get("name")}
    else:
        PROFILES = profiles_raw
    OPERATIONAL_CONFIG = raw.get("operational", {}) or {}
    REMOVE_ORIGINAL = bool(OPERATIONAL_CONFIG.get("remove_original_after_success", False))
    language_pref = OPERATIONAL_CONFIG.get("language_preferences") or DEFAULT_LANGUAGE_PREFERENCES
    LANGUAGE_PREFERENCES = tuple(str(lang).lower() for lang in language_pref if lang)


def _load_config_from_api() -> bool:
    try:
        response = httpx.get(
            f"{ORCHESTRATOR_URL}/api/config?ts={int(time.time())}",
            timeout=10.0,
            headers={"Cache-Control": "no-store"},
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Unable to fetch config from orchestrator: %s", exc)
        return False
    _hydrate_config(response.json())
    LOGGER.info("Loaded settings config from orchestrator (%s profiles)", len(PROFILES))
    return True


if not _load_config_from_api():
    _hydrate_config({"profiles": {"chromecast": BUILTIN_PROFILE}})
    LOGGER.info("Loaded built-in fallback profile (chromecast) because API was unreachable")
FFPROBE_ANALYSIS_CMD = [
    "ffprobe",
    "-hide_banner",
    "-loglevel",
    "warning",
    "-of",
    "json",
    "-show_format",
    "-show_streams",
]


def _probe_gpu_devices() -> tuple[list[str], list[str]]:
    devices: list[str] = []
    errors: list[str] = []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            name = line.strip()
            if name:
                devices.append(name)
    except FileNotFoundError:
        errors.append("nvidia-smi not installed")
    except subprocess.SubprocessError as exc:
        errors.append(f"nvidia-smi probe failed: {exc}")

    if not devices:
        for device in sorted(Path("/dev").glob("nvidia[0-9]*")):
            devices.append(device.name)

    return devices, errors


def _probe_ffmpeg_support() -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    support: dict[str, Any] = {
        "cuda": False,
        "nvenc": False,
        "tonemap_cuda": False,
        "scale_cuda": False,
        "scale_npp": False,
        "hwupload_cuda": False,
    }
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-hwaccels"],
            check=True,
            capture_output=True,
            text=True,
        )
        support["cuda"] = any("cuda" == line.strip().lower() for line in result.stdout.splitlines())
    except subprocess.SubprocessError as exc:
        errors.append(f"ffmpeg hwaccel probe failed: {exc}")

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-encoders"],
            check=True,
            capture_output=True,
            text=True,
        )
        support["nvenc"] = "h264_nvenc" in result.stdout
    except subprocess.SubprocessError as exc:
        errors.append(f"ffmpeg encoder probe failed: {exc}")

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-filters"],
            check=True,
            capture_output=True,
            text=True,
        )
        filters: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith(" "):
                filters.add(parts[1])
        support["tonemap_cuda"] = "tonemap_cuda" in filters
        support["scale_cuda"] = "scale_cuda" in filters
        support["scale_npp"] = "scale_npp" in filters
        support["hwupload_cuda"] = "hwupload_cuda" in filters
    except subprocess.SubprocessError as exc:
        errors.append(f"ffmpeg filter probe failed: {exc}")

    return support, errors


def snapshot_gpu_state() -> dict[str, Any]:
    devices, device_errors = _probe_gpu_devices()
    ffmpeg_support, ffmpeg_errors = _probe_ffmpeg_support()
    message_parts = device_errors + ffmpeg_errors
    message = "; ".join(message_parts)
    available = bool(devices) and ffmpeg_support.get("cuda") and ffmpeg_support.get("nvenc")
    if not available and not message:
        message = "CUDA devices or NVENC encoder not detected"
    return {
        "available": available,
        "devices": devices,
        "cuda_available": bool(ffmpeg_support.get("cuda")),
        "nvenc_available": bool(ffmpeg_support.get("nvenc")),
        "message": message,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tonemap_cuda_available": bool(ffmpeg_support.get("tonemap_cuda")),
    }


def require_gpu(gpu_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = gpu_state or snapshot_gpu_state()
    if not state.get("available"):
        message = state.get("message") or "GPU unavailable for NVENC workloads"
        raise RuntimeError(message)
    return state


def _derive_bit_depth(stream: dict) -> int | None:
    bits = stream.get("bits_per_raw_sample")
    if bits:
        try:
            return int(bits)
        except (TypeError, ValueError):
            pass
    pix_fmt = stream.get("pix_fmt") or ""
    matches = re.findall(r"(\d+)", pix_fmt)
    if matches:
        try:
            return int(matches[-1])
        except ValueError:
            return None
    return None


def _detect_hdr(stream: dict) -> bool:
    transfer = (stream.get("color_transfer") or "").lower()
    if transfer in {"smpte2084", "arib-std-b67"}:
        return True
    for item in stream.get("side_data_list") or []:
        if item.get("side_data_type", "").lower() in {
            "mastering display metadata",
            "content light level metadata",
            "hdr_dynamic_metadata",
        }:
            return True
    bit_depth = _derive_bit_depth(stream) or 8
    return bit_depth > 8


def _augment_analysis_with_video_metadata(analysis: dict) -> dict:
    streams = analysis.get("streams") or []
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        stream["bit_depth"] = _derive_bit_depth(stream)
        stream["is_hdr"] = _detect_hdr(stream)
    analysis["streams"] = streams
    return analysis


def probe_file(filepath: str | Path) -> dict:
    target_path = resolve_media_path(filepath)
    command = [*FFPROBE_ANALYSIS_CMD, str(target_path)]
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=FFPROBE_TIMEOUT,
            )
            try:
                raw = json.loads(result.stdout)
                return _augment_analysis_with_video_metadata(raw)
            except json.JSONDecodeError:
                LOGGER.warning("Failed to parse ffprobe output for %s", filepath)
                return {}
        except subprocess.TimeoutExpired:
            LOGGER.error(
                "ffprobe analysis timed out after %ss for %s",
                FFPROBE_TIMEOUT,
                target_path,
            )
            return {}
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            lowered = stderr.lower()
            if "moov atom not found" in lowered:
                LOGGER.info(
                    "ffprobe skipped %s (file still being written): %s",
                    target_path,
                    stderr or exc,
                )
            else:
                LOGGER.warning(
                    "ffprobe analysis failed for %s (exit %s): %s",
                    target_path,
                    exc.returncode,
                    stderr or exc,
                )
            if attempt < attempts and "moov atom not found" in stderr.lower():
                time.sleep(0.5 * attempt)
                continue
            return {}
        except subprocess.SubprocessError as exc:
            LOGGER.warning("ffprobe analysis failed for %s: %s", target_path, exc)
            if attempt < attempts:
                time.sleep(0.5 * attempt)
                continue
            return {}
    return {}


def _loggable_command(command: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in command)


def _media_summary(analysis: dict) -> dict:
    streams = analysis.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {}) or {}
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    return {
        "video_codec": video.get("codec_name"),
        "pix_fmt": video.get("pix_fmt"),
        "bit_depth": video.get("bit_depth") or video.get("bits_per_raw_sample"),
        "is_hdr": video.get("is_hdr"),
        "avg_frame_rate": video.get("avg_frame_rate"),
        "resolution": (
            f"{video.get('width')}x{video.get('height')}"
            if video.get("width") and video.get("height")
            else None
        ),
        "audio_streams": len(audio_streams),
        "subtitle_streams": len(subtitle_streams),
    }


def _extract_filter_chain(command: list[str]) -> str | None:
    if "-vf" not in command:
        return None
    try:
        index = command.index("-vf")
        return command[index + 1]
    except (ValueError, IndexError):
        return None


def classify_ffmpeg_error(logs: list[str], return_code: int) -> dict[str, str | bool | int]:
    """Categorize common ffmpeg failures for clearer UI messaging."""
    patterns = [
        (r"10[- ]?bit|yuv420p10", "unsupported_bit_depth", False),
        (r"pix_fmt [^ ]+ is not supported", "unsupported_pixel_format", False),
        (r"device busy|resource temporarily unavailable", "device_busy", True),
        (r"nvenc.+not available|no capable devices", "nvenc_unavailable", True),
        (r"Invalid data found when processing input", "corrupt_input", False),
        (r"moov atom not found", "incomplete_file", True),
        (r"No such filter: 'tonemap_cuda'", "unsupported_filter", False),
    ]
    tail = "\n".join(logs[-5:]) if logs else ""
    for pattern, category, retryable in patterns:
        if re.search(pattern, tail, re.IGNORECASE):
            return {
                "category": category,
                "retryable": retryable,
                "message": tail.splitlines()[-1] if tail else "",
                "return_code": return_code,
            }
    return {
        "category": "unknown",
        "retryable": False,
        "message": logs[-1] if logs else "",
        "return_code": return_code,
    }


async def _terminate_process(process: Process, reason: str, ffmpeg_logs: deque[str]) -> None:
    if reason:
        ffmpeg_logs.append(reason)
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


async def run_conversion(
    command: list[str],
    progress_callback,
    *,
    timeout: int | None = None,
    idle_timeout: int | None = None,
) -> tuple[int, list[str]]:
    LOGGER.info("Starting FFmpeg with command: %s", _loggable_command(command))
    ffmpeg_logs: deque[str] = deque(maxlen=100)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=PIPE,
            stderr=STDOUT,
        )
    except FileNotFoundError as exc:
        LOGGER.error("FFmpeg binary not found: %s", exc)
        return 127, ["ffmpeg not found"]

    loop = asyncio.get_running_loop()
    start = loop.time()
    last_output = start

    assert process.stdout is not None
    while True:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
        except asyncio.TimeoutError:
            now = loop.time()
            if timeout and now - start > timeout:
                await _terminate_process(
                    process,
                    f"FFmpeg timed out after {timeout}s",
                    ffmpeg_logs,
                )
                return -9, list(ffmpeg_logs)
            if idle_timeout and now - last_output > idle_timeout:
                await _terminate_process(
                    process,
                    f"FFmpeg produced no output for {idle_timeout}s",
                    ffmpeg_logs,
                )
                return -9, list(ffmpeg_logs)
            continue
        if not line:
            break
        last_output = loop.time()
        text_line = line.decode("utf-8", errors="replace").strip()
        if text_line.startswith("out_time_ms="):
            try:
                out_time_ms = int(text_line.split("=", 1)[1])
            except ValueError:
                continue
            progress_callback(out_time_ms)
        else:
            LOGGER.debug("ffmpeg: %s", text_line)
            ffmpeg_logs.append(text_line)

    return_code = await process.wait()
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


async def fetch_profile_settings(client: httpx.AsyncClient, profile_id: int) -> dict:
    try:
        response = await client.get(f"/api/profiles/{profile_id}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        LOGGER.warning("Failed to fetch profile %s: %s", profile_id, exc)
        return {}


async def resolve_encoding_for_job(job: dict, client: httpx.AsyncClient) -> dict:
    encoding = job.get("encoding")
    profile_id = job.get("profile_id")
    if not encoding and profile_id is not None:
        encoding = await fetch_profile_settings(client, profile_id)
    if not encoding:
        encoding = PROFILES.get(job.get("profile"), {})
    return encoding or {}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def claim_job(client: httpx.AsyncClient) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    try:
        response = await client.get("/api/jobs/next", params={"worker_id": WORKER_ID})
        if response.status_code == 204:
            return None, None
        response.raise_for_status()
        data = response.json()
        delivery_id = data.pop("delivery_id")
        return delivery_id, data
    except httpx.HTTPError as exc:
        LOGGER.warning("Failed to claim job: %s", exc)
        return None, None


async def acknowledge_job(client: httpx.AsyncClient, job_id: str, delivery_id: str) -> None:
    try:
        await client.post(f"/api/jobs/{job_id}/ack", json={"delivery_id": delivery_id})
    except httpx.HTTPError as exc:
        LOGGER.error("Failed to acknowledge job %s: %s", job_id[:8], exc)


async def queue_paused(client: httpx.AsyncClient) -> Tuple[bool, Optional[str]]:
    try:
        response = await client.get("/api/queue/state")
        response.raise_for_status()
        state = response.json()
        return state.get("paused", False), state.get("reason")
    except httpx.HTTPError:
        return False, None


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def update_job_status(
    client: httpx.AsyncClient,
    job_id: str,
    status: str,
    progress: int,
    message: str | None = None,
    *,
    return_code: int | None = None,
    logs: list[str] | None = None,
    pipeline: dict | None = None,
) -> None:
    payload = {"status": status, "progress": progress}
    if message:
        payload["message"] = message
    if return_code is not None:
        payload["return_code"] = return_code
    if logs is not None:
        payload["logs"] = logs
    if pipeline is not None:
        payload["pipeline"] = pipeline
    try:
        response = await client.post(f"/api/jobs/{job_id}/status", json=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        LOGGER.error("Failed to update job %s status: %s", job_id[:8], exc)


async def publish_worker_telemetry(
    client: httpx.AsyncClient, worker_id: str, gpu_state: dict[str, Any]
) -> None:
    payload = {
        "worker_id": worker_id,
        "gpu_available": bool(gpu_state.get("available")),
        "devices": gpu_state.get("devices") or [],
        "cuda_available": bool(gpu_state.get("cuda_available")),
        "nvenc_available": bool(gpu_state.get("nvenc_available")),
        "checked_at": gpu_state.get("checked_at") or datetime.now(timezone.utc).isoformat(),
        "message": gpu_state.get("message"),
    }
    try:
        await client.post("/api/workers/telemetry", json=payload)
    except httpx.HTTPError as exc:
        LOGGER.debug("Failed to publish GPU telemetry: %s", exc)


async def telemetry_loop(client: httpx.AsyncClient, worker_id: str) -> None:
    while True:
        try:
            gpu_state = snapshot_gpu_state()
            await publish_worker_telemetry(client, worker_id, gpu_state)
        except Exception as exc:
            LOGGER.warning("Telemetry loop error: %s", exc)
        await asyncio.sleep(max(GPU_TELEMETRY_INTERVAL, 5))


async def _ensure_gpu_ready(client: httpx.AsyncClient, job_id: str) -> bool:
    gpu_state = snapshot_gpu_state()
    try:
        require_gpu(gpu_state)
    except RuntimeError as exc:
        message = f"GPU unavailable: {exc}"
        LOGGER.error("%s", message)
        await publish_worker_telemetry(client, WORKER_ID, gpu_state)
        await update_job_status(client, job_id, "failed", 0, message)
        return False
    return True


async def _probe_duration(source: Path) -> float:
    analysis = await asyncio.to_thread(probe_file, source)
    try:
        return float(analysis.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        LOGGER.debug("Unable to parse duration from ffprobe output for %s", source)
        return 0.0


async def _validate_output(output: Path, expected_duration: float) -> bool:
    if not output.exists():
        LOGGER.warning("Output validation failed: File %s not found", output)
        return False
    try:
        stat = output.stat()
    except OSError as exc:
        LOGGER.warning("Output validation failed: stat error %s", exc)
        return False
    if stat.st_size <= 0:
        LOGGER.warning("Output validation failed: File empty")
        return False
    if expected_duration > 0:
        output_duration = await _probe_duration(output)
        if output_duration <= 0:
            LOGGER.warning("Output validation failed: Duration probe failed (0s)")
            return False
        if abs(output_duration - expected_duration) > 1.0:
            LOGGER.warning(
                "Duration mismatch: Source %.2fs, Output %.2fs (Diff %.2fs)",
                expected_duration,
                output_duration,
                abs(output_duration - expected_duration),
            )
            return False
    return True


def _build_output_path(source: Path) -> Path:
    resolved = resolve_media_path(source)
    return resolved.parent / f"{resolved.stem}-chromecast.mp4"


async def _maybe_remove_original(source: Path, output_path: Path, expected_duration: float) -> bool:
    source = resolve_media_path(source)
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


async def handle_delete_job(client: httpx.AsyncClient, job: dict) -> None:
    job_id = job["id"]
    source_path = job["path"]
    request_id = job.get("request_id")
    token = _current_request_id.set(request_id)
    try:
        LOGGER.info("Processing DELETE job %s for %s", job_id[:8], source_path)
        path = resolve_media_path(source_path)
        if not path.exists():
            await update_job_status(client, job_id, "completed", 100, "File already missing")
            return

        if path.is_dir():
            raise ValueError("Path is a directory")

        path.unlink()
        await update_job_status(client, job_id, "completed", 100, "File deleted")
        LOGGER.info("Deleted file %s", path)
    except Exception as exc:
        LOGGER.error("Delete job failed: %s", exc)
        await update_job_status(client, job_id, "failed", 0, str(exc))
    finally:
        _current_request_id.reset(token)


async def process_job(client: httpx.AsyncClient, job: dict) -> None:  # noqa: C901
    job_id = job["id"]
    if job.get("job_type") == "delete":
        await handle_delete_job(client, job)
        return

    request_id = job.get("request_id")
    token = _current_request_id.set(request_id)
    try:
        source = job["path"]
        playback_target = resolve_media_path(source)
        LOGGER.info("Picked up job %s for %s (resolved %s)", job_id[:8], source, playback_target)
        await update_job_status(client, job_id, "running", 5, "Allocated to GPU worker")
        if not playback_target.exists():
            message = f"Source file not found: {source}"
            LOGGER.error("%s", message)
            await update_job_status(client, job_id, "failed", 0, message)
            return

        if not await _ensure_gpu_ready(client, job_id):
            return

        analysis = await asyncio.to_thread(probe_file, playback_target)
        analysis = analysis or {}
        duration = _extract_duration(analysis)
        LOGGER.info("Media analysis for %s: %s", job_id[:8], _media_summary(analysis))
        if duration == 0:
            LOGGER.warning("Duration probe for %s returned 0 seconds", playback_target)

        output_path = _build_output_path(playback_target)
        encoding = await resolve_encoding_for_job(job, client)
        if not encoding:
            LOGGER.warning(
                "No encoding settings supplied for profile %s; using defaults.",
                job.get("profile"),
            )

        pipeline = job.get("pipeline")
        if not pipeline and isinstance(encoding, dict):
            pipeline = encoding.get("pipeline")

        analysis["encoding"] = encoding
        analysis["profile"] = job.get("profile")
        if pipeline:
            analysis["pipeline"] = pipeline

        if not job.get("force", False) and await _validate_output(output_path, duration):
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

        builder = FFmpegBuilder(
            analysis,
            playback_target,
            output_path,
            PROFILES,
            FFMPEG_CAPABILITIES,
            HOST_ENVIRONMENT,
            language_preferences=LANGUAGE_PREFERENCES,
        )
        try:
            command = builder.build()
        except RuntimeError as exc:
            message = f"Validation failed: {exc}"
            LOGGER.error("%s", message)
            await update_job_status(client, job_id, "failed", 0, message)
            return

        pipeline_info = analysis.get("pipeline") or {}
        LOGGER.info(
            "Job %s pipeline: decode=%s scale=%s encode=%s attempt=%s/%s",
            job_id[:8],
            pipeline_info.get("decode_type"),
            pipeline_info.get("scale_type"),
            pipeline_info.get("encode_type"),
            pipeline_info.get("attempt"),
            pipeline_info.get("max_attempts"),
        )

        filter_chain = _extract_filter_chain(command)
        if filter_chain:
            LOGGER.info("Job %s video filters: %s", job_id[:8], filter_chain)

        loop = asyncio.get_running_loop()
        progress_callback, _, _ = _progress_callback_factory(duration, loop, client, job_id)

        return_code, ffmpeg_logs = await run_conversion(
            command,
            progress_callback,
            timeout=FFMPEG_TIMEOUT,
            idle_timeout=FFMPEG_IDLE_TIMEOUT,
        )

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
            classification = classify_ffmpeg_error(ffmpeg_logs, return_code)
            message = f"FFmpeg exited with code {return_code}"
            if ffmpeg_logs:
                LOGGER.error(
                    "Job %s failed (code %s). Last FFmpeg output:\n%s",
                    job_id[:8],
                    return_code,
                    "\n".join(ffmpeg_logs),
                )
                message = (
                    f"{message}; {classification.get('category')}: "
                    f"{classification.get('message')}"
                )
            if classification.get("category") == "nvenc_unavailable":
                FFMPEG_CAPABILITIES.refresh()
            await update_job_status(
                client,
                job_id,
                "failed",
                0,
                message,
                return_code=return_code,
                logs=ffmpeg_logs,
                pipeline=pipeline_info or pipeline,
            )
    finally:
        _current_request_id.reset(token)


async def main() -> None:
    LOGGER.info(
        "GPU worker starting; polling %s every %ss (log level %s)",
        ORCHESTRATOR_URL,
        POLL_INTERVAL,
        LOG_LEVEL,
    )
    last_pause_reason: Optional[str] = None
    async with httpx.AsyncClient(base_url=ORCHESTRATOR_URL, timeout=30.0) as client:
        telemetry_task = asyncio.create_task(telemetry_loop(client, WORKER_ID))
        try:
            while True:
                paused, reason = await queue_paused(client)
                if paused:
                    if reason != last_pause_reason:
                        LOGGER.warning("Job queue paused: %s", reason or "no reason provided")
                        last_pause_reason = reason
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                last_pause_reason = None

                delivery_id, job = await claim_job(client)
                if not job or not delivery_id:
                    LOGGER.debug("No job available; sleeping for %ss", POLL_INTERVAL)
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                try:
                    await process_job(client, job)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("Job %s failed: %s", job["id"][:8], exc)
                    await update_job_status(client, job["id"], "failed", 0, str(exc))
                finally:
                    await acknowledge_job(client, job["id"], delivery_id)
                await asyncio.sleep(1)
        finally:
            telemetry_task.cancel()
            with suppress(asyncio.CancelledError):
                await telemetry_task


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    LOGGER.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


sys.excepthook = handle_exception


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        LOGGER.critical("Fatal error in worker main loop", exc_info=exc)
        sys.exit(1)
