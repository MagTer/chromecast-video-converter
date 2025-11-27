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
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yaml
from ffmpeg_builder import FFmpegBuilder

logging.addLevelName(logging.DEBUG, "VERBOSE")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL == "VERBOSE":
    LOG_LEVEL = "DEBUG"
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:9000")
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
WATCH_PREFIX = Path("/watch")
MEDIA_PREFIX = Path("/media")


def _resolve_media_path(path: str | Path) -> Path:
    normalized = Path(str(path).replace("\\", "/"))
    try:
        relative = normalized.relative_to(WATCH_PREFIX)
    except ValueError:
        return normalized
    return MEDIA_PREFIX / relative


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
    target_path = _resolve_media_path(filepath)
    command = [*FFPROBE_ANALYSIS_CMD, str(target_path)]
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                LOGGER.warning("Failed to parse ffprobe output for %s", filepath)
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


async def claim_job(client: httpx.AsyncClient) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    try:
        response = await client.get("/api/jobs/next")
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
    resolved = _resolve_media_path(source)
    return resolved.parent / f"{resolved.stem}-chromecast.mp4"


async def _maybe_remove_original(source: Path, output_path: Path, expected_duration: float) -> bool:
    source = _resolve_media_path(source)
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
    playback_target = _resolve_media_path(source)
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

    builder = FFmpegBuilder(PROFILES, HOST_ENVIRONMENT, NVENC_CAPABILITIES)
    command = builder.build_command(analysis, playback_target, output_path)

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

if __name__ == "__main__":
    asyncio.run(main())
