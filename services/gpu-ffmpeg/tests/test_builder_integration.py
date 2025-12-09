import sys
from pathlib import Path
from typing import Iterable

sys.path.append(str(Path(__file__).parents[1]))

from app.capabilities import EncoderCapabilities, FfmpegCapabilities
from app.ffmpeg_builder import FFmpegBuilder


def make_capabilities(
    filters: Iterable[str] | None = None,
    hwaccels: Iterable[str] | None = None,
) -> FfmpegCapabilities:
    default_filters = {
        "tonemap_cuda",
        "scale_npp",
        "scale_cuda",
        "hwupload_cuda",
        "zscale",
        "tonemap",
    }
    encoder_info = EncoderCapabilities(
        name="h264_nvenc",
        rc_modes=frozenset(["vbr", "vbr_hq", "cq", "cbr"]),
        multipass_modes=frozenset(["fullres"]),
        supports_lookahead=True,
    )
    return FfmpegCapabilities(
        skip_detection=True,
        filters=set(filters or default_filters),
        encoders={"h264_nvenc", "libx264", "aac"},
        decoders={"h264", "hevc", "aac"},
        hwaccels=set(hwaccels or ["cuda"]),
        encoder_capabilities={"h264_nvenc": encoder_info},
    )


PROFILES = {
    "chromecast": {
        "name": "chromecast",
        "gpu": {
            "codec": "h264",
            "profile": "high",
            "level": "4.1",
            "max_fps": 30,
            "rc": "vbr_hq",
            "bitrate": "5M",
            "max_bitrate": "10M",
            "bufsize": "16M",
            "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
        },
        "cpu": {
            "codec": "h264",
            "profile": "high",
            "level": "4.1",
            "max_fps": 30,
            "rc": "crf",
            "cq": 20,
            "preset": "slow",
            "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
        },
    }
}


def test_standard_8bit_gpu_pipeline(tmp_path):
    """
    Scenario: 8-bit H.264/HEVC input.
    Expected: Decode(GPU) -> scale_npp -> Encode(GPU). No hwdownload.
    """
    analysis = {
        "profile": "chromecast",
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",  # 8-bit
                "bits_per_raw_sample": "8",
                "avg_frame_rate": "24000/1001",
                "width": 1920,
                "height": 1080,
            },
            {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
        ],
    }

    builder = FFmpegBuilder(
        analysis,
        tmp_path / "in.mkv",
        tmp_path / "out.mp4",
        PROFILES,
        make_capabilities(),
        {"is_wsl2": False},
    )
    command = builder.build()

    # Check Input
    assert "-hwaccel" in command
    assert command[command.index("-hwaccel") + 1] == "cuda"

    # Check Filter Chain
    vf_idx = command.index("-vf")
    vf = command[vf_idx + 1]
    assert "scale_npp" in vf
    assert "hwdownload" not in vf
    assert "hwupload" not in vf

    # Check Encoder
    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "h264_nvenc"

    # Check Output maps
    map_indices = [i for i, x in enumerate(command) if x == "-map"]
    maps = [command[i + 1] for i in map_indices]
    assert "0:v" in maps

    # Verify no stray pix_fmt (fixed bug)
    assert "-pix_fmt" not in command


def test_hdr_10bit_gpu_pipeline(tmp_path):
    """
    Scenario: 10-bit HDR HEVC input.
    Expected: Decode(GPU) -> hwdownload -> zscale(CPU) -> scale(CPU) -> hwupload -> Encode(GPU).
    Reason: _gpu_scale_filter returns None for > 8-bit, forcing CPU fallback.
    """
    analysis = {
        "profile": "chromecast",
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "pix_fmt": "yuv420p10le",  # 10-bit
                "bits_per_raw_sample": "10",
                "color_transfer": "smpte2084",  # HDR
                "avg_frame_rate": "24000/1001",
                "width": 3840,
                "height": 2160,
            },
            {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
        ],
    }

    builder = FFmpegBuilder(
        analysis,
        tmp_path / "in.mkv",
        tmp_path / "out.mp4",
        PROFILES,
        make_capabilities(),
        {"is_wsl2": False},
    )
    command = builder.build()

    # Check Filter Chain
    vf_idx = command.index("-vf")
    vf = command[vf_idx + 1]

    assert "hwdownload" in vf
    assert "format=p010le" in vf
    assert "zscale=t=linear" in vf  # CPU Tonemapping
    assert "hwupload_cuda" in vf

    # Ensure scale_npp/cuda are NOT used (logic prevents them for 10-bit)
    assert "scale_npp" not in vf
    assert "scale_cuda" not in vf

    # Check Encoder
    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "h264_nvenc"


def test_cpu_fallback_retry(tmp_path):
    """
    Scenario: Orchestrator schedules a CPU retry.
    Expected: Use libx264, no GPU flags.
    """
    analysis = {
        "profile": "chromecast",
        "streams": [{"codec_type": "video", "pix_fmt": "yuv420p"}],
        # Pipeline override representing a retry
        "pipeline": {"decode_type": "cpu", "scale_type": "cpu", "encode_type": "cpu"},
    }

    builder = FFmpegBuilder(
        analysis,
        tmp_path / "in.mkv",
        tmp_path / "out.mp4",
        PROFILES,
        make_capabilities(),
        {"is_wsl2": False},
    )
    command = builder.build()

    assert "-hwaccel" not in command

    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "libx264"

    assert "-crf" in command
    assert command[command.index("-crf") + 1] == "20"

    assert "-preset" in command
    assert command[command.index("-preset") + 1] == "slow"

    assert "h264_nvenc" not in command


def test_embedded_subtitles(tmp_path):
    """
    Scenario: Subtitle streams present, sidecars disabled (default).
    Expected: Maps subtitle streams and sets codec to mov_text.
    """
    analysis = {
        "profile": "chromecast",
        "skip_embedded_subtitles": False,  # Explicitly allowing embedding
        "streams": [
            {"codec_type": "video"},
            {"codec_type": "audio", "tags": {"language": "eng"}},
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "swe"},
                "index": 3,
            },
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng"},
                "index": 4,
            },
        ],
    }
    builder = FFmpegBuilder(
        analysis,
        tmp_path / "in.mkv",
        tmp_path / "out.mp4",
        PROFILES,
        make_capabilities(),
        {"is_wsl2": False},
    )
    command = builder.build()

    # Check maps
    map_indices = [i for i, x in enumerate(command) if x == "-map"]
    maps = [command[i + 1] for i in map_indices]
    # Logic picks best track, so we expect one of the subtitle indices
    assert "0:s:0" in maps or "0:s:1" in maps

    # Check codec
    assert "-c:s" in command
    assert command[command.index("-c:s") + 1] == "mov_text"

    # Ensure no sidecar maps (srt output)
    # We check if there are any other outputs. The last arg is output path.
    # The arg before it should be part of main options.
    # If sidecars were present, we'd see '-map ... -c:s srt ... path.srt' sequence.

    assert "srt" not in command
