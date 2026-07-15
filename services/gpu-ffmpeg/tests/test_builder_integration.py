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
        "scale_npp",
        "scale_cuda",
        "hwupload_cuda",
        "zscale",
        "tonemap",
        "bwdif",
        "yadif",
        "bwdif_cuda",
        "yadif_cuda",
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


def make_profiles(resolution: str | None = None) -> dict:
    gpu: dict = {
        "codec": "h264",
        "profile": "high",
        "level": "4.1",
        "max_fps": 30,
        "rc": "vbr_hq",
        "bitrate": "5M",
        "max_bitrate": "10M",
        "bufsize": "16M",
        "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
    }
    cpu: dict = {
        "codec": "h264",
        "profile": "high",
        "level": "4.1",
        "max_fps": 30,
        "rc": "crf",
        "cq": 20,
        "preset": "slow",
        "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
    }
    if resolution is not None:
        gpu["resolution"] = resolution
        cpu["resolution"] = resolution
    return {"chromecast": {"name": "chromecast", "gpu": gpu, "cpu": cpu}}


PROFILES = make_profiles()

GPU_SCALE_1080 = (
    "scale_npp=w=min(iw\\,1920):h=min(ih\\,1080):"
    "force_original_aspect_ratio=decrease:force_divisible_by=2:format=nv12"
)


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

    # Check Filter Chain: 1920x1080 source exceeds the 1280x720 default target,
    # so it must be downscaled with both axes clamped.
    vf_idx = command.index("-vf")
    vf = command[vf_idx + 1]
    assert "scale_npp=w=min(iw\\,1280):h=min(ih\\,720)" in vf
    assert "force_original_aspect_ratio=decrease" in vf
    assert "force_divisible_by=2" in vf
    assert "hwdownload" not in vf
    assert "hwupload" not in vf

    # Check Encoder
    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "h264_nvenc"

    # Check Output maps (0:V excludes attached pictures)
    map_indices = [i for i, x in enumerate(command) if x == "-map"]
    maps = [command[i + 1] for i in map_indices]
    assert "0:V:0" in maps

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

    # CPU scale must clamp both axes without upscaling
    assert "scale=w=min(iw\\,1280):h=min(ih\\,720)" in vf
    assert "force_original_aspect_ratio=decrease" in vf
    assert "force_divisible_by=2" in vf

    # Ensure scale_npp/cuda are NOT used (logic prevents them for 10-bit)
    assert "scale_npp" not in vf
    assert "scale_cuda" not in vf

    # Check Encoder
    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "h264_nvenc"

    # Tonemapped output must be tagged as BT.709
    assert command[command.index("-colorspace:v") + 1] == "bt709"
    assert command[command.index("-color_primaries:v") + 1] == "bt709"
    assert command[command.index("-color_trc:v") + 1] == "bt709"


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


def _build_command(analysis, tmp_path, profiles=None, capabilities=None):
    builder = FFmpegBuilder(
        analysis,
        tmp_path / "in.mkv",
        tmp_path / "out.mp4",
        profiles or PROFILES,
        capabilities or make_capabilities(),
        {"is_wsl2": False},
    )
    return builder.build()


def _video_analysis(**overrides):
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "bits_per_raw_sample": "8",
        "avg_frame_rate": "24000/1001",
        "width": 1920,
        "height": 1080,
    }
    stream.update(overrides)
    return {
        "profile": "chromecast",
        "streams": [
            stream,
            {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
        ],
    }


def _vf(command):
    if "-vf" not in command:
        return None
    return command[command.index("-vf") + 1]


def test_cropped_widescreen_source_is_left_untouched(tmp_path):
    """
    Scenario: 1920x800 cropped 2.4:1 rip with a 1080p profile.
    The old scale=-2:1080 upscaled this to 2592x1080, which exceeds both the
    Chromecast decoder width limit and H.264 level 4.1. The source already
    fits the 1920x1080 box, so it must not be scaled at all.
    """
    analysis = _video_analysis(width=1920, height=800)
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1920x1080"))

    vf = _vf(command)
    assert vf is None or "scale" not in vf


def test_ultrawide_source_is_capped_on_both_axes(tmp_path):
    """A 2560x1080 ultrawide source exceeds the width cap and must be scaled down."""
    analysis = _video_analysis(width=2560, height=1080)
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1920x1080"))

    vf = _vf(command)
    assert vf is not None
    assert GPU_SCALE_1080 in vf


def test_source_within_target_is_not_scaled(tmp_path):
    """A 1280x720 source already fits a 1080p profile: no scaling, no upscale."""
    analysis = _video_analysis(width=1280, height=720)
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1920x1080"))

    vf = _vf(command)
    assert vf is None or "scale" not in vf


def test_sd_source_is_never_upscaled(tmp_path):
    analysis = _video_analysis(width=720, height=576, avg_frame_rate="25/1")
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1920x1080"))

    vf = _vf(command)
    assert vf is None or "scale" not in vf


def test_profile_resolution_above_chromecast_limit_is_clamped(tmp_path):
    """A misconfigured 4K profile must still be clamped to 1920x1080."""
    analysis = _video_analysis(width=3840, height=2160)
    command = _build_command(analysis, tmp_path, profiles=make_profiles("3840x2160"))

    vf = _vf(command)
    assert vf is not None
    assert "min(iw\\,1920)" in vf
    assert "min(ih\\,1080)" in vf
    assert "min(iw\\,3840)" not in vf


def test_malformed_profile_resolution_falls_back_to_default(tmp_path):
    analysis = _video_analysis(width=3840, height=2160)
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1080p"))

    vf = _vf(command)
    assert vf is not None
    assert "min(iw\\,1280)" in vf
    assert "min(ih\\,720)" in vf


def test_unknown_source_dimensions_keep_safe_scale_filter(tmp_path):
    """Without probed dimensions the clamping (never-upscaling) filter stays on."""
    analysis = _video_analysis(width=None, height=None)
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1920x1080"))

    vf = _vf(command)
    assert vf is not None
    assert GPU_SCALE_1080 in vf


def test_interlaced_source_is_deinterlaced_on_gpu(tmp_path):
    analysis = _video_analysis(width=1280, height=720, field_order="tt")
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1920x1080"))

    vf = _vf(command)
    assert vf is not None
    assert "bwdif_cuda=mode=send_frame" in vf
    assert "hwdownload" not in vf


def test_interlaced_source_falls_back_to_cpu_deinterlace(tmp_path):
    capabilities = make_capabilities(
        filters={"scale_npp", "scale_cuda", "hwupload_cuda", "zscale", "tonemap", "bwdif", "yadif"}
    )
    analysis = _video_analysis(width=1280, height=720, field_order="tt")
    command = _build_command(
        analysis, tmp_path, profiles=make_profiles("1920x1080"), capabilities=capabilities
    )

    vf = _vf(command)
    assert vf is not None
    assert "bwdif=mode=send_frame" in vf
    assert "hwdownload" in vf
    assert "hwupload_cuda" in vf


def test_attached_picture_is_not_mapped_or_encoded(tmp_path):
    analysis = {
        "profile": "chromecast",
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "mjpeg",
                "disposition": {"attached_pic": 1},
                "width": 600,
                "height": 900,
            },
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "24000/1001",
                "width": 1920,
                "height": 1080,
            },
            {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
        ],
    }
    command = _build_command(analysis, tmp_path, profiles=make_profiles("1920x1080"))

    map_indices = [i for i, x in enumerate(command) if x == "-map"]
    maps = [command[i + 1] for i in map_indices]
    assert maps.count("0:V:0") == 1
    assert all(not m.startswith("0:v") for m in maps if m != "0:V:0")

    # The real video stream (1920x1080) fits the target, so no scaling either.
    vf = _vf(command)
    assert vf is None or "scale" not in vf


def test_max_fps_is_clamped_to_chromecast_limit(tmp_path):
    profiles = make_profiles("1920x1080")
    profiles["chromecast"]["gpu"]["max_fps"] = 120
    analysis = _video_analysis(width=1920, height=1080, avg_frame_rate="120/1")
    command = _build_command(analysis, tmp_path, profiles=profiles)

    vf = _vf(command)
    assert vf is not None
    assert "fps=60" in vf
