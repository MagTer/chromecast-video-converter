import sys
from pathlib import Path
from typing import Iterable

# Add services/gpu-ffmpeg to path so we can import app
sys.path.append(str(Path(__file__).parents[1]))

from app import utils
from app.capabilities import EncoderCapabilities, FfmpegCapabilities
from app.ffmpeg_builder import FFmpegBuilder


def make_capabilities(
    *,
    rc_modes: Iterable[str] | None = None,
    multipass_modes: Iterable[str] | None = None,
    filters: Iterable[str] | None = None,
    hwaccels: Iterable[str] | None = None,
    supports_lookahead: bool = True,
) -> FfmpegCapabilities:
    rc_modes_set = frozenset(rc_modes or ["vbr", "vbr_hq", "cq", "constqp", "cbr"])
    multipass_set = frozenset(multipass_modes or ["fullres"])
    encoder_info = EncoderCapabilities(
        name="h264_nvenc",
        rc_modes=rc_modes_set,
        multipass_modes=multipass_set,
        supports_lookahead=supports_lookahead,
    )
    default_filters = {
        "tonemap_cuda",
        "scale_npp",
        "scale_cuda",
        "hwupload_cuda",
        "zscale",
        "tonemap",
    }
    return FfmpegCapabilities(
        skip_detection=True,
        filters=set(filters or default_filters),
        encoders={"h264_nvenc"},
        decoders=set(),
        hwaccels=set(hwaccels or ["cuda"]),
        encoder_capabilities={"h264_nvenc": encoder_info},
    )


def test_detect_host_environment_wsl(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    # We assume /proc/version check fails or returns something innocuous
    # Since we can't easily mock file read without pyfakefs or similar,
    # we rely on env var check fallback

    env = utils.detect_host_environment()
    assert env["is_wsl2"] is True


def test_build_ffmpeg_command_maps_streams(tmp_path):
    profiles = {
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

    input_path = tmp_path / "input.mkv"
    # input_path.write_bytes(b"") # Not needed for builder
    output_path = tmp_path / "output.mp4"

    analysis = {
        "profile": "mobile",
        "streams": [
            {"codec_type": "video"},
            {
                "codec_type": "audio",
                "tags": {"language": "swe"},
                "disposition": {"default": 1},
            },
            {"codec_type": "audio", "tags": {"language": "eng"}, "disposition": {}},
            {
                "codec_type": "subtitle",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
        ],
    }

    # Mock capabilities: VBR HQ unavailable to force fallback test.
    capabilities = make_capabilities(rc_modes=["vbr", "cbr"])
    host_env = {"is_wsl2": False}

    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()

    assert command[:2] == ["ffmpeg", "-y"]
    assert "-c:v" in command
    # h264_nvenc might be checked by checking usage of -c:v h264_nvenc
    assert command[command.index("-c:v") + 1] == "h264_nvenc"

    assert "-rc" in command
    assert command[command.index("-rc") + 1] == "vbr"  # Fallback confirmed
    assert "-multipass" not in command

    assert "-b:v" in command and command[command.index("-b:v") + 1] == "5M"
    assert "-bf" in command
    assert "-rc-lookahead" in command
    assert command[command.index("-rc-lookahead") + 1] == "10"

    # Map checks
    assert "-map" in command
    # Simple contains check is not enough for order/specific maps
    # Original: assert "-map" in command and "0:v" in command
    assert "0:v" in command
    assert "0:a:0" in command
    assert "0:a:1" in command
    assert "-pix_fmt" in command
    assert command[command.index("-pix_fmt") + 1] == "nv12"

    assert "-c:a" in command and command[command.index("-c:a") + 1] == "aac"
    assert "-aq-strength" in command and command[command.index("-aq-strength") + 1] == "7"
    assert "-disposition:a:0" in command
    assert command[command.index("-disposition:a:0") + 1] == "default"

    assert "-c:s" in command and command[command.index("-c:s") + 1] == "mov_text"
    assert command[-1] == str(output_path)


def test_build_ffmpeg_command_respects_frame_limits(tmp_path):
    profiles = {
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
            "aq_strength": 9,
            "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
            "resolution": "1920x1080",
        }
    }

    capabilities = make_capabilities()
    host_env = {"is_wsl2": False}

    input_path = tmp_path / "movie.mkv"
    output_path = tmp_path / "movie.mp4"
    analysis = {"profile": "cinema", "streams": [{"codec_type": "video"}]}

    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()

    vf_index = command.index("-vf")
    vf_val = command[vf_index + 1]
    assert "fps=24" in vf_val
    assert "scale_npp=w=-2:h=1080:format=nv12:force_original_aspect_ratio=decrease" in vf_val
    assert "-pix_fmt" in command and command[command.index("-pix_fmt") + 1] == "nv12"

    assert "-rc" in command and command[command.index("-rc") + 1] == "vbr_hq"
    assert "-multipass" in command and command[command.index("-multipass") + 1] == "fullres"
    assert "-aq-strength" in command and command[command.index("-aq-strength") + 1] == "9"


def test_build_ffmpeg_command_honors_source_frame_rate(tmp_path):
    profiles = {
        "adaptive": {
            "max_bitrate": "8M",
            "bufsize": "12M",
            "level": "4.1",
            "profile": "high",
            "max_fps": 30,
            "preset": "p5",
            "rc": "vbr",
            "cq": 19,
            "lookahead": 10,
            "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
        }
    }

    capabilities = make_capabilities()
    host_env = {"is_wsl2": False}

    input_path = tmp_path / "series.mkv"
    output_path = tmp_path / "series.mp4"
    analysis = {
        "profile": "adaptive",
        "streams": [
            {"codec_type": "video", "avg_frame_rate": "24000/1001"},
        ],
    }

    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()

    vf_index = command.index("-vf")
    vf_val = command[vf_index + 1]
    assert vf_val.startswith("scale_npp")
    assert "fps=" not in vf_val


def test_build_ffmpeg_command_clamps_high_frame_rate(tmp_path):
    profiles = {
        "adaptive": {
            "max_bitrate": "8M",
            "bufsize": "12M",
            "level": "4.1",
            "profile": "high",
            "max_fps": 30,
            "preset": "p5",
            "rc": "vbr",
            "cq": 19,
            "lookahead": 10,
            "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
        }
    }

    capabilities = make_capabilities()
    host_env = {"is_wsl2": False}

    input_path = tmp_path / "series60.mkv"
    output_path = tmp_path / "series60.mp4"
    analysis = {
        "profile": "adaptive",
        "streams": [
            {"codec_type": "video", "avg_frame_rate": "60000/1001"},
        ],
    }

    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()

    vf_index = command.index("-vf")
    vf_val = command[vf_index + 1]
    assert "fps=30" in vf_val


def test_build_ffmpeg_command_respects_profile_level_and_aq(tmp_path):
    profiles = {}
    profiles["baseline"] = {
        "bitrate": "6M",
        "max_bitrate": "8M",
        "bufsize": "12M",
        "level": "3.1",
        "profile": "baseline",
        "max_fps": 30,
        "preset": "p5",
        "rc": "cq",
        "cq": 20,
        "bframes": 2,  # will be forced to 0 for baseline
        "lookahead": 0,
        "adaptive_b_frames": True,
        "aq": False,
        "spatial_aq": False,
        "temporal_aq": False,
        "audio": {"codec": "aac", "bitrate": "160k", "channels": 2},
        "resolution": "1280x720",
    }

    capabilities = make_capabilities()
    host_env = {"is_wsl2": False}

    input_path = tmp_path / "clip.mkv"
    output_path = tmp_path / "clip.mp4"
    analysis = {
        "profile": "baseline",
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    }

    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()

    assert "-profile:v" in command and command[command.index("-profile:v") + 1] == "baseline"
    assert "-level" in command and command[command.index("-level") + 1] == "3.1"

    bf_index = command.index("-bf")
    assert command[bf_index + 1] == "0"

    assert "-b_adapt" in command and command[command.index("-b_adapt") + 1] == "0"
    assert "-rc-lookahead" in command and command[command.index("-rc-lookahead") + 1] == "0"

    assert "-rc" in command and command[command.index("-rc") + 1] == "constqp"
    assert "-qp" in command and command[command.index("-qp") + 1] == "20"

    assert "-b:v" not in command  # CQ should not emit VBR flags

    assert "-spatial_aq" in command and command[command.index("-spatial_aq") + 1] == "0"
    assert "-temporal_aq" in command and command[command.index("-temporal_aq") + 1] == "0"
    assert "-aq-strength" not in command

    assert "-c:a" in command and command[command.index("-c:a") + 1] == "aac"
    assert "-ac" in command and command[command.index("-ac") + 1] == "2"


def test_build_ffmpeg_command_adds_tonemap_for_hdr(tmp_path):
    profiles = {
        "hdr": {
            "bitrate": "8M",
            "max_bitrate": "10M",
            "bufsize": "16M",
            "max_fps": 30,
            "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
        }
    }
    capabilities = make_capabilities()
    host_env = {"is_wsl2": False}
    input_path = tmp_path / "hdr.mkv"
    output_path = tmp_path / "hdr.mp4"
    analysis = {
        "profile": "hdr",
        "streams": [
            {
                "codec_type": "video",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
                "avg_frame_rate": "30000/1001",
            }
        ],
    }
    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()
    vf_val = command[command.index("-vf") + 1]
    assert "tonemap_cuda" in vf_val


def test_subtitles_extracted_before_gpu_pipeline(tmp_path):
    profiles = {
        "chromecast": {
            "bitrate": "8M",
            "max_bitrate": "8M",
            "bufsize": "16M",
            "level": "4.1",
            "profile": "high",
            "max_fps": 30,
            "audio": {"codec": "aac", "bitrate": "192k", "channels": 2},
        }
    }
    capabilities = make_capabilities()
    host_env = {"is_wsl2": False}
    analysis = {
        "profile": "chromecast",
        "streams": [
            {
                "codec_type": "video",
                "pix_fmt": "yuv420p10le",
                "bits_per_raw_sample": "10",
                "avg_frame_rate": "24000/1001",
            },
            {
                "codec_type": "subtitle",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
        ],
        "skip_embedded_subtitles": True,
    }

    builder = FFmpegBuilder(
        analysis,
        tmp_path / "input.mkv",
        tmp_path / "output.mp4",
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()

    vf_val = command[command.index("-vf") + 1]
    assert "hwdownload" not in vf_val
    assert "hwupload_cuda" not in vf_val
    assert "tonemap_cuda" in vf_val
    assert "scale_npp" in vf_val
    assert "0:s" not in command

    assert "-c:v" in command and command[command.index("-c:v") + 1] == "h264_nvenc"


def test_hdr_uses_cpu_tonemap_when_cuda_missing(tmp_path):
    profiles = {
        "hdr": {
            "audio": {"codec": "aac", "bitrate": "192k"},
        }
    }
    host_env = {"is_wsl2": False}
    analysis = {
        "profile": "hdr",
        "streams": [
            {
                "codec_type": "video",
                "pix_fmt": "p010le",
                "color_transfer": "smpte2084",
                "avg_frame_rate": "30000/1001",
            }
        ],
    }
    capabilities = make_capabilities(
        filters={"scale_npp", "scale_cuda", "hwupload_cuda", "zscale", "tonemap"}
    )
    builder = FFmpegBuilder(
        analysis,
        tmp_path / "in.mkv",
        tmp_path / "out.mp4",
        profiles,
        capabilities,
        host_env,
    )
    command = builder.build()
    vf_val = command[command.index("-vf") + 1]
    assert "tonemap=hable" in vf_val
    assert "tonemap_cuda" not in vf_val
    assert "hwdownload" in vf_val
