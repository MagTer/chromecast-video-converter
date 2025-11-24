from __future__ import annotations

import importlib
import subprocess
import types

import pytest


@pytest.fixture()
def worker_module(monkeypatch):
    def fake_run(command, check=True, capture_output=True, text=True):  # noqa: ANN001, ANN204
        if "-hwaccels" in command:
            return types.SimpleNamespace(stdout="cuda\n", stderr="")
        if "-encoders" in command:
            return types.SimpleNamespace(stdout="V..... h264_nvenc\n", stderr="")
        if "encoder=h264_nvenc" in " ".join(command):
            return types.SimpleNamespace(stdout="fullres\nvbr_hq\n", stderr="")
        raise FileNotFoundError("ffmpeg stub invoked unexpectedly")

    monkeypatch.setattr(subprocess, "run", fake_run)

    import worker

    return importlib.reload(worker)


def test_build_ffmpeg_command_maps_streams(worker_module, tmp_path):
    worker = worker_module
    worker.NVENC_CAPABILITIES = {"rc_vbr_hq": False, "multipass_fullres": False}
    worker.PROFILES.clear()
    worker.PROFILES.update(
        {
            "mobile": {
                "bitrate": "5M",
                "max_bitrate": "6M",
                "bufsize": "12M",
                "level": "4.1",
                "profile": "high",
                "max_fps": 30,
                "preset": "p4",
                "rc": "vbr_hq",
                "cq": 19,
                "bframes": 2,
                "lookahead": 10,
                "adaptive_b_frames": True,
                "aq": True,
                "audio": {"codec": "aac", "bitrate": "160k", "channels": 2},
                "resolution": "1280x720",
            }
        }
    )

    input_path = tmp_path / "input.mkv"
    input_path.write_bytes(b"")
    output_path = tmp_path / "output.mp4"

    analysis = {
        "profile": "mobile",
        "streams": [
            {"codec_type": "video"},
            {"codec_type": "audio", "tags": {"language": "swe"}, "disposition": {"default": 1}},
            {"codec_type": "audio", "tags": {"language": "eng"}, "disposition": {}},
            {"codec_type": "subtitle", "tags": {"language": "eng"}, "disposition": {"default": 1}},
        ],
    }

    command = worker.build_ffmpeg_command(analysis, input_path, output_path)

    assert command[:2] == ["ffmpeg", "-y"]
    assert "-c:v" in command and "h264_nvenc" in command
    assert "-rc" in command and "vbr" in command  # falls back when vbr_hq unavailable
    assert "-b:v" in command and "5M" in command
    assert "-bf" in command
    assert "-look_ahead_depth" in command
    assert "-map" in command and "0:v" in command
    assert "-map" in command and "0:a:0" in command and "0:a:1" in command
    assert "-c:a" in command and "aac" in command
    assert "-disposition:a:0" in command and "default" in command
    assert "-c:s" in command and "mov_text" in command
    assert command[-1] == str(output_path)


def test_build_ffmpeg_command_respects_frame_limits(worker_module, tmp_path):
    worker = worker_module
    worker.NVENC_CAPABILITIES = {"rc_vbr_hq": True, "multipass_fullres": True}
    worker.PROFILES.clear()
    worker.PROFILES.update(
        {
            "cinema": {
                "max_bitrate": "10M",
                "bufsize": "20M",
                "level": "4.1",
                "profile": "high",
                "max_fps": 24,
                "preset": "p7",
                "rc": "vbr_hq",
                "cq": 17,
                "lookahead": 24,
                "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
                "resolution": "1920x1080",
            }
        }
    )

    input_path = tmp_path / "movie.mkv"
    input_path.write_bytes(b"")
    output_path = tmp_path / "movie.mp4"

    analysis = {"profile": "cinema", "streams": [{"codec_type": "video"}]}
    command = worker.build_ffmpeg_command(analysis, input_path, output_path)

    vf_index = command.index("-vf")
    assert "fps=24" in command[vf_index + 1]
    assert "scale_cuda=-2:1080" in command[vf_index + 1]
    assert "-multipass" in command
    assert "-rc" in command and "vbr_hq" in command
