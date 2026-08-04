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


def test_augment_analysis_10bit_sdr_is_not_hdr():
    analysis = {
        "streams": [
            {
                "codec_type": "video",
                "pix_fmt": "yuv420p10le",
                # No colour transfer or HDR side data: 10-bit SDR rip
            }
        ]
    }
    enriched = _augment_analysis_with_video_metadata(analysis)
    assert enriched["streams"][0]["is_hdr"] is False
    assert enriched["streams"][0]["bit_depth"] == 10


def test_probe_file_handles_timeout(monkeypatch, tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"fake")

    def fake_run(*args, **kwargs):  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=worker.FFPROBE_TIMEOUT)

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    result = worker.probe_file(target)
    assert result == {}


def test_run_conversion_filters_progress_noise(monkeypatch):
    lines = [
        b"[hevc @ 0x1] decoder warning\n",
        b"frame=  100 fps= 25 q=18.0 size=1024KiB\n",
        b"fps=25.00\n",
        b"stream_0_0_q=18.0\n",
        b"bitrate=5000kbits/s\n",
        b"total_size=1048576\n",
        b"out_time_us=1000000\n",
        b"out_time_ms=1000000\n",
        b"out_time=00:00:01.000000\n",
        b"dup_frames=0\n",
        b"drop_frames=0\n",
        b"speed=1.5x\n",
        b"progress=continue\n",
        b"Conversion failed!\n",
        b"",  # EOF
    ]

    class FakeStdout:
        def __init__(self, items):
            self._items = list(items)

        async def readline(self):
            return self._items.pop(0)

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout(lines)

        async def wait(self):
            return 0

    async def fake_create(*args, **kwargs):  # noqa: ARG001
        return FakeProcess()

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", fake_create)

    progress_ticks: list[int] = []
    code, logs = asyncio.run(worker.run_conversion(["ffmpeg"], progress_ticks.append))

    assert code == 0
    # Progress telemetry must reach the callback but stay out of the log buffer
    assert progress_ticks == [1000000]
    assert logs == ["[hevc @ 0x1] decoder warning", "Conversion failed!"]


def test_run_conversion_handles_missing_binary(monkeypatch):
    async def fake_create(*args, **kwargs):  # noqa: ARG001
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", fake_create)
    code, logs = asyncio.run(worker.run_conversion(["ffmpeg"], lambda _: None))

    assert code == 127
    assert logs == ["ffmpeg not found"]
