import asyncio
import json
import logging
import os
import shlex
import subprocess
import time
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as redis
import yaml

logging.addLevelName(logging.DEBUG, "VERBOSE")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL == "VERBOSE":
    LOG_LEVEL = "DEBUG"
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:9000")
JOB_QUEUE_URL = os.environ.get("JOB_QUEUE", "redis://redis:6379/0")
JOB_QUEUE_STREAM = os.environ.get("JOB_QUEUE_STREAM", "job_queue")
JOB_QUEUE_GROUP = os.environ.get("JOB_QUEUE_GROUP", "workers")
JOB_VISIBILITY_TIMEOUT = int(os.environ.get("JOB_VISIBILITY_TIMEOUT", "300"))
STREAM_READER_LIMIT = int(os.environ.get("GPU_STREAM_READER_LIMIT", "1000000"))
WORKER_ID = os.environ.get("WORKER_ID", f"worker-{os.getpid()}")


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
    """Detect whether we're running under WSL2 (for NVENC quirks)."""

    try:
        version = Path("/proc/version").read_text().lower()
        if "microsoft" in version or "wsl2" in version:
            return {"is_wsl2": True}
    except OSError:
        pass

    is_wsl2 = any(os.environ.get(var) for var in ("WSL_DISTRO_NAME", "WSL_INTEROP"))
    return {"is_wsl2": is_wsl2}


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
    except FileNotFoundError as exc:
        LOGGER.warning("ffmpeg not found while probing NVENC capabilities: %s", exc)
        return capabilities
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

if HOST_ENVIRONMENT.get("is_wsl2"):
    LOGGER.warning(
        "Detected WSL2 environment; NVENC rate-control and multipass support may be limited"
    )
    if NVENC_CAPABILITIES.get("rc_vbr_hq") or NVENC_CAPABILITIES.get("multipass_fullres"):
        NVENC_CAPABILITIES["rc_vbr_hq"] = False
        NVENC_CAPABILITIES["multipass_fullres"] = False
        LOGGER.warning(
            "Disabling NVENC VBR HQ and multipass; WSL2 drivers often reject that combination. "
            "Worker will fall back to single-pass VBR."
        )

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/settings.yaml"))
PROFILES: dict = {}
OPERATIONAL_CONFIG: dict = {}
REMOVE_ORIGINAL = False
GPU_TELEMETRY_INTERVAL = int(os.environ.get("GPU_TELEMETRY_INTERVAL", "30"))


def _hydrate_config(raw: dict) -> None:
    global PROFILES, OPERATIONAL_CONFIG, REMOVE_ORIGINAL
    profiles_raw = raw.get("profiles", {}) or {}
    if isinstance(profiles_raw, list):
        PROFILES = {item.get("name"): item for item in profiles_raw if item.get("name")}
    else:
        PROFILES = profiles_raw
    OPERATIONAL_CONFIG = raw.get("operational", {}) or {}
    REMOVE_ORIGINAL = bool(OPERATIONAL_CONFIG.get("remove_original_after_success", False))


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


def _load_config_from_disk() -> None:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        LOGGER.warning("No settings config present at %s; using defaults", CONFIG_PATH)
        return
    _hydrate_config(raw)
    LOGGER.info(
        "Loaded settings config from %s (%s profiles available)", CONFIG_PATH, len(PROFILES)
    )


if not _load_config_from_api():
    _load_config_from_disk()
FFPROBE_ANALYSIS_CMD = [
    "ffprobe",
    "-v",
    "quiet",
    "-print_format",
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
    support: dict[str, Any] = {"cuda": False, "nvenc": False}
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
    }


def require_gpu(gpu_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = gpu_state or snapshot_gpu_state()
    if not state.get("available"):
        message = state.get("message") or "GPU unavailable for NVENC workloads"
        raise RuntimeError(message)
    return state


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


def _scaling_expression(profile: dict) -> str:
    resolution = profile.get("max_resolution") or profile.get("resolution")
    if resolution:
        try:
            _, height_str = resolution.lower().split("x", 1)
            height = int(height_str)
            if height > 0:
                return f"scale_cuda=-2:{height}:force_original_aspect_ratio=decrease"
        except (ValueError, AttributeError):
            LOGGER.debug("Invalid resolution %s; falling back to default scale", resolution)
    return SCALING_EXPRESSION


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
                    "title": stream.get("tags", {}).get("title"),
                }
            )
            audio_pos += 1
        elif codec_type == "subtitle":
            subtitle_streams.append(
                {
                    "input_index": subtitle_pos,
                    "language": _normalize_language(stream.get("tags", {}).get("language")),
                    "disposition": stream.get("disposition", {}),
                    "title": stream.get("tags", {}).get("title"),
                }
            )
            subtitle_pos += 1
    return video_present, audio_streams, subtitle_streams


def _is_commentary(stream: dict) -> bool:
    disposition = stream.get("disposition", {}) or {}
    title = (stream.get("title") or "").lower()
    return bool(disposition.get("comment") or "commentary" in title)


def _pick_best_stream(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    original = next((s for s in candidates if s.get("disposition", {}).get("original")), None)
    if original:
        return original
    default = next((s for s in candidates if s.get("disposition", {}).get("default")), None)
    if default:
        return default
    return candidates[0]


def _select_priority_streams(stream_list: list[dict]) -> tuple[list[dict], int | None]:
    mapped: list[dict] = []
    seen_inputs: set[int] = set()

    def _pick_for_language(language: str) -> dict | None:
        language_matches = [s for s in stream_list if s.get("language") == language]
        if not language_matches:
            return None
        preferred = [s for s in language_matches if not _is_commentary(s)]
        return _pick_best_stream(preferred or language_matches)

    swedish = _pick_for_language("swe")
    english = _pick_for_language("eng")

    non_commentary = [s for s in stream_list if not _is_commentary(s)]
    original_fallback = _pick_best_stream(non_commentary or stream_list)

    for candidate in [swedish, english, original_fallback]:
        if candidate is None:
            continue
        idx = candidate["input_index"]
        if idx in seen_inputs:
            continue
        mapped.append(candidate)
        seen_inputs.add(idx)

    default_idx: int | None = None
    if swedish is not None and swedish in mapped:
        default_idx = mapped.index(swedish)
    elif english is not None and english in mapped:
        default_idx = mapped.index(english)
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


def _ffmpeg_base_command(input_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-hwaccel",
        "cuda",
        "-hwaccel_output_format",
        "cuda",
        "-i",
        str(input_path),
    ]


def build_ffmpeg_command(  # noqa: C901
    analysis_json: dict, input_path: Path, output_path: Path
) -> list[str]:
    profile = analysis_json.get("encoding") or PROFILES.get(
        analysis_json.get("profile"),
        {},
    )

    profile = profile or {}
    bitrate = profile.get("bitrate") or profile.get("max_bitrate", "8M")
    maxrate = profile.get("max_bitrate", bitrate)
    bufsize = profile.get("bufsize", "16M")
    level = profile.get("level", "4.1")
    h264_profile = str(profile.get("profile", "high"))
    h264_profile_lower = h264_profile.lower()
    max_fps = max(1, int(profile.get("max_fps", 30) or 30))
    preset = str(profile.get("preset", "p6"))
    rc_mode = str(profile.get("rc", "vbr") or "vbr").lower()
    if rc_mode in {"cbr", "vbr_hq"}:
        rc_mode = "vbr"
    cq = str(profile.get("cq", 18))
    bframes = int(profile.get("bframes", 2) or 0)
    if h264_profile_lower == "baseline":
        bframes = 0
    lookahead = int(profile.get("lookahead", 24) or 0)
    adaptive_b_frames = bool(profile.get("adaptive_b_frames", True))
    adaptive_b_frames = adaptive_b_frames and lookahead > 0 and bframes > 0
    if h264_profile_lower == "baseline":
        adaptive_b_frames = False
    aq_enabled = bool(profile.get("aq", True))
    spatial_aq = aq_enabled and bool(profile.get("spatial_aq", True))
    temporal_aq = aq_enabled and bool(profile.get("temporal_aq", True))
    aq_strength = int(profile.get("aq_strength", 7) or 7)
    aq_strength = max(1, min(15, aq_strength))
    multipass_mode: str | None = None
    if rc_mode == "vbr_hq":
        multipass_mode = "fullres"

    if rc_mode == "vbr_hq" and not NVENC_CAPABILITIES.get("rc_vbr_hq", True):
        LOGGER.warning(
            "Requested rc mode vbr_hq is unavailable; falling back to vbr (WSL2=%s)",
            HOST_ENVIRONMENT.get("is_wsl2"),
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
    audio_channels = 2  # Chromecast-safe stereo only

    streams = analysis_json.get("streams", [])
    video_present, audio_streams, subtitle_streams = _gather_streams(streams)

    selected_audio, default_audio_idx = _select_priority_streams(audio_streams)
    selected_subtitles, default_sub_idx = _select_priority_streams(subtitle_streams)

    command: list[str] = _ffmpeg_base_command(input_path)

    if video_present:
        command.extend(["-map", "0:v"])

    for audio_stream in selected_audio:
        command.extend(["-map", f"0:a:{audio_stream['input_index']}"])

    for subtitle_stream in selected_subtitles:
        command.extend(["-map", f"0:s:{subtitle_stream['input_index']}"])

    audio_dispositions = _build_disposition_flags(selected_audio, default_audio_idx, "a")
    subtitle_dispositions = _build_disposition_flags(selected_subtitles, default_sub_idx, "s")

    filters = [_scaling_expression(profile)]
    if max_fps > 0:
        filters.append(f"fps={max_fps}")
    video_filter = ",".join(filters)

    command.extend(
        [
            "-vf",
            video_filter,
            "-c:v",
            "h264_nvenc",
            "-preset",
            preset,
            "-profile:v",
            h264_profile,
            "-level",
            level,
        ]
    )

    if rc_mode == "cq":
        command.extend(["-rc", "constqp", "-qp", cq])
    else:
        command.extend(["-rc", rc_mode, "-b:v", bitrate, "-maxrate", maxrate, "-bufsize", bufsize])
        if multipass_mode:
            command.extend(["-multipass", multipass_mode])

    command.extend(["-bf", str(bframes)])

    if lookahead > 0:
        command.extend(["-look_ahead", "1", "-look_ahead_depth", str(lookahead)])
    else:
        command.extend(["-look_ahead", "0"])

    command.extend(["-b_adapt", "1" if adaptive_b_frames else "0"])
    command.extend(
        [
            "-spatial_aq",
            "1" if spatial_aq else "0",
            "-temporal_aq",
            "1" if temporal_aq else "0",
        ]
    )
    if aq_enabled:
        command.extend(["-aq-strength", str(aq_strength)])
    command.extend(["-movflags", "+faststart"])

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

    if selected_subtitles:
        command.extend(["-c:s", "mov_text"])

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


async def ensure_queue(redis_client: redis.Redis) -> None:
    try:
        await redis_client.xgroup_create(JOB_QUEUE_STREAM, JOB_QUEUE_GROUP, id="0-0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _normalize_job(payload: dict) -> dict:
    normalized = dict(payload)
    if isinstance(normalized.get("encoding"), str):
        try:
            normalized["encoding"] = json.loads(normalized["encoding"])
        except json.JSONDecodeError:
            normalized["encoding"] = {}
    if normalized.get("profile_id") not in (None, ""):
        try:
            normalized["profile_id"] = int(normalized["profile_id"])
        except (TypeError, ValueError):
            normalized["profile_id"] = None
    normalized["progress"] = int(normalized.get("progress") or 0)
    return normalized


async def _claim_stalled(redis_client: redis.Redis, consumer: str) -> tuple[str, dict] | None:
    resp = await redis_client.xautoclaim(
        JOB_QUEUE_STREAM,
        JOB_QUEUE_GROUP,
        consumer,
        min_idle_time=JOB_VISIBILITY_TIMEOUT * 1000,
        start_id="0-0",
        count=1,
    )
    # redis-py returns (next_id, messages); some RESP3/older variants return
    # (next_id, messages, deleted)
    if isinstance(resp, tuple):
        if len(resp) == 2:
            _next_id, messages = resp
        elif len(resp) >= 3:
            _next_id, messages, _deleted = resp[0], resp[1], resp[2]
        else:
            messages = []
    else:  # fallback: treat as sequence
        try:
            _next_id, messages = resp[0], resp[1]
        except Exception:  # noqa: BLE001
            messages = []
    if not messages:
        return None
    entry_id, fields = messages[0]
    payload = json.loads(fields.get("payload", "{}"))
    return entry_id, _normalize_job(payload)


async def claim_job(redis_client: redis.Redis, consumer: str) -> tuple[str, dict] | None:
    pending = await _claim_stalled(redis_client, consumer)
    if pending:
        return pending

    result = await redis_client.xreadgroup(
        JOB_QUEUE_GROUP, consumer, {JOB_QUEUE_STREAM: ">"}, count=1, block=1000
    )
    if not result:
        return None
    _, messages = result[0]
    entry_id, fields = messages[0]
    payload = json.loads(fields.get("payload", "{}"))
    return entry_id, _normalize_job(payload)


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
        gpu_state = snapshot_gpu_state()
        await publish_worker_telemetry(client, worker_id, gpu_state)
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


async def acknowledge_job(client: httpx.AsyncClient, job_id: str, delivery_id: str) -> None:
    try:
        await client.post(f"/api/jobs/{job_id}/ack", json={"delivery_id": delivery_id})
    except httpx.HTTPError as exc:
        LOGGER.error("Failed to acknowledge job %s: %s", job_id[:8], exc)


async def queue_paused(redis_client: redis.Redis) -> tuple[bool, str | None]:
    paused = await redis_client.get(f"{JOB_QUEUE_STREAM}:paused")
    reason = await redis_client.get(f"{JOB_QUEUE_STREAM}:pause_reason")
    return bool(int(paused)) if paused is not None else False, reason


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


async def process_job(client: httpx.AsyncClient, job: dict) -> None:  # noqa: C901
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

    if not await _ensure_gpu_ready(client, job_id):
        return

    analysis = await asyncio.to_thread(probe_file, playback_target)
    analysis = analysis or {}
    duration = _extract_duration(analysis)
    if duration == 0:
        LOGGER.warning("Duration probe for %s returned 0 seconds", playback_target)

    output_path = _build_output_path(playback_target)
    encoding = await resolve_encoding_for_job(job, client)
    if not encoding:
        LOGGER.warning(
            "No encoding settings supplied for profile %s; using defaults.",
            job.get("profile"),
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
    consumer_id = WORKER_ID
    redis_client = redis.from_url(JOB_QUEUE_URL, decode_responses=True)
    await ensure_queue(redis_client)
    last_pause_reason: str | None = None
    async with httpx.AsyncClient(base_url=ORCHESTRATOR_URL, timeout=30.0) as client:
        telemetry_task = asyncio.create_task(telemetry_loop(client, consumer_id))
        try:
            while True:
                paused, reason = await queue_paused(redis_client)
                if paused:
                    if reason != last_pause_reason:
                        LOGGER.warning("Job queue paused: %s", reason or "no reason provided")
                        last_pause_reason = reason
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                last_pause_reason = None

                claimed = await claim_job(redis_client, consumer_id)
                if not claimed:
                    LOGGER.debug("No job available; sleeping for %ss", POLL_INTERVAL)
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                delivery_id, job = claimed
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
            await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
