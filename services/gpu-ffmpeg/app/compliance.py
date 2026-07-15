"""Chromecast Gen 2/3 compliance evaluation of transcoded outputs.

Evaluates an ffprobe analysis dict against the hard device limits:
H.264 (Baseline/Main/High) up to level 4.2, max 1920x1080, max 60 fps
(30 fps at level 4.1 for 1080p), 8-bit SDR yuv420p, AAC stereo audio,
MP4 container.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .ffmpeg_builder import VideoStreamInfo

MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_FPS = 60.05
MAX_LEVEL = 42
ALLOWED_H264_PROFILES = {"baseline", "constrained baseline", "main", "high"}
ALLOWED_PIX_FMTS = {"yuv420p", "yuvj420p"}


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def compliance_failure(issues: list[str]) -> dict:
    return {"compliant": False, "issues": issues, "checked_at": _checked_at()}


def _minimum_level(width: int, height: int, fps: float | None) -> int | None:
    """Return minimum H.264 level (as tens-int, e.g. 41) for the frame size/rate."""
    if not fps:
        return None
    if width <= 1280 and height <= 720:
        return 31 if fps <= 30.05 else 40
    if width <= MAX_WIDTH and height <= MAX_HEIGHT:
        return 41 if fps <= 30.05 else 42
    return None


def evaluate_chromecast_compliance(analysis: dict) -> dict:  # noqa: C901
    if not analysis:
        return compliance_failure(["Output file could not be probed"])

    issues: list[str] = []
    video_summary: dict = {}

    format_name = ((analysis.get("format") or {}).get("format_name") or "").lower()
    # MP4 outputs probe as the "mov,mp4,m4a,..." demuxer family.
    if format_name and "mp4" not in format_name:
        issues.append(f"Container is not MP4 ({format_name})")

    streams = analysis.get("streams") or []
    videos = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video"
        and not (stream.get("disposition") or {}).get("attached_pic")
    ]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]

    if not videos:
        issues.append("No video stream found")
    else:
        if len(videos) > 1:
            issues.append(f"Multiple video streams ({len(videos)})")
        video = videos[0]
        info = VideoStreamInfo(
            input_index=0,
            codec_name=video.get("codec_name"),
            pix_fmt=video.get("pix_fmt"),
            bits_per_raw_sample=video.get("bits_per_raw_sample"),
            color_transfer=video.get("color_transfer"),
            side_data_list=video.get("side_data_list"),
            avg_frame_rate=video.get("avg_frame_rate"),
            r_frame_rate=video.get("r_frame_rate"),
            width=video.get("width"),
            height=video.get("height"),
            field_order=video.get("field_order"),
        )
        codec = (info.codec_name or "").lower()
        width = info.width or 0
        height = info.height or 0
        fps = info.frame_rate()
        profile = (video.get("profile") or "").lower()
        level = video.get("level")
        level = int(level) if isinstance(level, (int, float)) and level > 0 else None

        video_summary = {
            "codec": codec or None,
            "width": width or None,
            "height": height or None,
            "fps": round(fps, 3) if fps else None,
            "profile": profile or None,
            "level": level,
            "pix_fmt": info.pix_fmt,
        }

        if codec != "h264":
            issues.append(f"Video codec is {codec or 'unknown'}, not H.264")
        else:
            if profile and profile not in ALLOWED_H264_PROFILES:
                issues.append(f"H.264 profile {profile} is not Chromecast-safe")
            if level is not None and level > MAX_LEVEL:
                issues.append(f"H.264 level {level / 10:.1f} exceeds 4.2")
            required_level = _minimum_level(width, height, fps) if width and height else None
            if level is not None and required_level is not None and level < required_level:
                issues.append(
                    f"H.264 level {level / 10:.1f} is too low for "
                    f"{width}x{height} @ {fps:.4g} fps (needs {required_level / 10:.1f})"
                )
        if width > MAX_WIDTH:
            issues.append(f"Width {width} exceeds {MAX_WIDTH}")
        if height > MAX_HEIGHT:
            issues.append(f"Height {height} exceeds {MAX_HEIGHT}")
        if fps and fps > MAX_FPS:
            issues.append(f"Frame rate {fps:.4g} exceeds 60 fps")
        bit_depth = info.bit_depth()
        if bit_depth and bit_depth > 8:
            issues.append(f"{bit_depth}-bit video; Chromecast requires 8-bit")
        if info.pix_fmt and info.pix_fmt not in ALLOWED_PIX_FMTS:
            issues.append(f"Pixel format {info.pix_fmt} is not yuv420p")
        if info.is_hdr():
            issues.append("HDR color transfer; Chromecast Gen 2/3 requires SDR (BT.709)")
        if info.is_interlaced():
            issues.append("Interlaced video")

    for audio in audios:
        codec = (audio.get("codec_name") or "").lower()
        channels = audio.get("channels")
        if codec not in {"aac", "mp3"}:
            issues.append(f"Audio codec {codec or 'unknown'} is not AAC")
        if isinstance(channels, int) and channels > 2:
            issues.append(f"Audio has {channels} channels; Chromecast plays stereo")

    return {
        "compliant": not issues,
        "issues": issues,
        "checked_at": _checked_at(),
        "video": video_summary or None,
    }
