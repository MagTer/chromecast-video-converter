import sys
from pathlib import Path

# Add services/gpu-ffmpeg to path so we can import app
sys.path.append(str(Path(__file__).parents[1]))

from app import utils
from app.ffmpeg_builder import FFmpegBuilder


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

    # Mock capabilities: VBR HQ unavailable to force fallback test?
    # Original test said: worker.NVENC_CAPABILITIES = {"rc_vbr_hq": False, ...}
    # And asserted "-rc" in command and "vbr" in command # falls back

    nvenc_capabilities = {"rc_vbr_hq": False, "multipass_fullres": False}
    host_env = {"is_wsl2": False}

    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        nvenc_capabilities,
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

    nvenc_capabilities = {"rc_vbr_hq": True, "multipass_fullres": True}
    host_env = {"is_wsl2": False}

    input_path = tmp_path / "movie.mkv"
    output_path = tmp_path / "movie.mp4"
    analysis = {"profile": "cinema", "streams": [{"codec_type": "video"}]}

    builder = FFmpegBuilder(
        analysis,
        input_path,
        output_path,
        profiles,
        nvenc_capabilities,
        host_env,
    )
    command = builder.build()

    vf_index = command.index("-vf")
    vf_val = command[vf_index + 1]
    assert "fps=24" in vf_val
    assert "scale_cuda=-2:1080:force_original_aspect_ratio=decrease" in vf_val
    assert "format=nv12" in vf_val
    assert "hwupload_cuda" in vf_val
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

    nvenc_capabilities = {"rc_vbr_hq": True, "multipass_fullres": True}
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
        nvenc_capabilities,
        host_env,
    )
    command = builder.build()

    vf_index = command.index("-vf")
    vf_val = command[vf_index + 1]
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

    nvenc_capabilities = {"rc_vbr_hq": True, "multipass_fullres": True}
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
        nvenc_capabilities,
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

    nvenc_capabilities = {"rc_vbr_hq": True, "multipass_fullres": True}
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
        nvenc_capabilities,
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
