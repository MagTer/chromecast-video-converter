# Add services/gpu-ffmpeg to path so we can import app
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[1]))

from app import worker  # type: ignore  # noqa: E402
from app.worker import (  # type: ignore  # noqa: E402
    _augment_analysis_with_video_metadata,
    classify_ffmpeg_error,
)


def test_classify_ffmpeg_error_retryable_and_category():
    logs = [
        "Some info",
        "Device busy or resource temporarily unavailable",
    ]
    result = classify_ffmpeg_error(logs, return_code=1)
    assert result["category"] == "device_busy"
    assert result["retryable"] is True


def test_classify_ffmpeg_error_unknown_defaults():
    result = classify_ffmpeg_error([], return_code=1)
    assert result["category"] == "unknown"
    assert result["retryable"] is False


def test_augment_analysis_adds_hdr_metadata():
    analysis = {
        "streams": [
            {
                "codec_type": "video",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
            }
        ]
    }
    enriched = _augment_analysis_with_video_metadata(analysis)
    assert enriched["streams"][0]["is_hdr"] is True
    assert enriched["streams"][0]["bit_depth"] == 10


def test_probe_file_handles_timeout(monkeypatch, tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"fake")

    def fake_run(*args, **kwargs):  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=worker.FFPROBE_TIMEOUT)

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    result = worker.probe_file(target)
    assert result == {}


def test_run_conversion_handles_missing_binary(monkeypatch):
    async def fake_create(*args, **kwargs):  # noqa: ARG001
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", fake_create)
    code, logs = asyncio.run(worker.run_conversion(["ffmpeg"], lambda _: None))

    assert code == 127
    assert logs == ["ffmpeg not found"]


def test_extract_subtitle_track_handles_timeout(monkeypatch, tmp_path):
    source = tmp_path / "movie.mkv"
    dest = tmp_path / "movie.srt"
    source.write_bytes(b"fake")

    def fake_run(*args, **kwargs):  # noqa: ARG001
        raise subprocess.TimeoutExpired(
            cmd="ffmpeg", timeout=worker.SUBTITLE_EXTRACTION_TIMEOUT or 1
        )

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    assert worker._extract_subtitle_track(source, 0, dest) is False
