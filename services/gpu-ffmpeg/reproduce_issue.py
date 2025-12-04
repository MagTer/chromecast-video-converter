import sys
from pathlib import Path

sys.path.append("services/gpu-ffmpeg")

from app.capabilities import EncoderCapabilities, FfmpegCapabilities
from app.ffmpeg_builder import FFmpegBuilder


def make_capabilities() -> FfmpegCapabilities:
    encoder_info = EncoderCapabilities(
        name="h264_nvenc",
        rc_modes=frozenset(["vbr", "vbr_hq", "cq", "cbr"]),
        multipass_modes=frozenset(["fullres"]),
        supports_lookahead=True,
    )
    # Removing "zscale", "tonemap" from filters to simulate missing CPU tonemap support
    return FfmpegCapabilities(
        skip_detection=True,
        filters={"tonemap_cuda", "scale_npp", "scale_cuda", "hwupload_cuda"},
        encoders={"h264_nvenc", "libx264", "aac"},
        decoders={"h264", "hevc", "aac"},
        hwaccels={"cuda"},
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

analysis = {
    "profile": "chromecast",
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "hevc",
            "pix_fmt": "yuv420p10le",
            "bits_per_raw_sample": "10",
            "color_transfer": "smpte2084",
            "avg_frame_rate": "24000/1001",
            "width": 1920,
            "height": 1080,
        },
        {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
    ],
    "pipeline": {
        "decode_type": "cpu",
        "scale_type": "cpu",
        "encode_type": "cpu",
        "attempt": 5,
        "max_attempts": 5,
    },
}

builder = FFmpegBuilder(
    analysis, Path("in.mkv"), Path("out.mp4"), PROFILES, make_capabilities(), {"is_wsl2": False}
)
command = builder.build()
print(" ".join(command))
