import logging
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence

from .capabilities import EncoderCapabilities, FfmpegCapabilities

LOGGER = logging.getLogger("gpu-ffmpeg.builder")

DEFAULT_LANGUAGE_PREFERENCES = ("swe", "eng")

# Hard device limits for Chromecast Gen 2/3 (H.264 High @ L4.1/4.2).
# The hardware decoder rejects anything wider/taller than 1080p regardless
# of the H.264 level signalled in the stream.
CHROMECAST_MAX_WIDTH = 1920
CHROMECAST_MAX_HEIGHT = 1080
CHROMECAST_MAX_FPS = 60
DEFAULT_TARGET_RESOLUTION = (1280, 720)

INTERLACED_FIELD_ORDERS = {"tt", "bb", "tb", "bt"}

SUPPORTED_SUBTITLE_CODECS = {
    "subrip",
    "ass",
    "ssa",
    "webvtt",
    "mov_text",
    "stl",
    "srt",
    "text",
}


@dataclass
class StreamDisposition:
    default: bool = False
    original: bool = False
    comment: bool = False

    @classmethod
    def from_raw(cls, disposition: dict | None) -> "StreamDisposition":
        disposition = disposition or {}
        return cls(
            default=bool(disposition.get("default")),
            original=bool(disposition.get("original")),
            comment=bool(disposition.get("comment")),
        )


@dataclass
class VideoStreamInfo:
    input_index: int
    codec_name: str | None = None
    pix_fmt: str | None = None
    bits_per_raw_sample: str | int | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    side_data_list: list | None = None
    avg_frame_rate: str | None = None
    r_frame_rate: str | None = None
    width: int | None = None
    height: int | None = None
    field_order: str | None = None
    bit_rate: str | int | None = None

    def bit_depth(self) -> int | None:
        if self.bits_per_raw_sample:
            try:
                return int(self.bits_per_raw_sample)
            except (TypeError, ValueError):
                pass
        if self.pix_fmt:
            # Match common FFmpeg formats like yuv420p10le, p010le, etc.
            # We look for digits immediately followed by 'le' or 'be'.
            match = re.search(r"(\d+)(?:le|be)", self.pix_fmt)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass
        return None

    def frame_rate(self) -> float | None:
        for candidate in (self.avg_frame_rate, self.r_frame_rate):
            if not candidate:
                continue
            try:
                if "/" in candidate:
                    return float(Fraction(candidate))
                return float(candidate)
            except (ValueError, ZeroDivisionError):
                continue
        return None

    def is_interlaced(self) -> bool:
        return (self.field_order or "").lower() in INTERLACED_FIELD_ORDERS

    def video_bit_rate(self) -> int | None:
        try:
            value = int(self.bit_rate) if self.bit_rate is not None else 0
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def is_hdr(self) -> bool:
        if self.color_transfer and self.color_transfer.lower() in {
            "smpte2084",
            "arib-std-b67",
        }:
            return True
        if self.side_data_list:
            for item in self.side_data_list:
                if item.get("side_data_type", "").lower() in {
                    "mastering display metadata",
                    "content light level metadata",
                    "hdr_dynamic_metadata",
                }:
                    return True
        return False


@dataclass
class AudioStreamInfo:
    input_index: int
    language: str | None
    disposition: StreamDisposition
    title: str | None = None
    codec_name: str | None = None


@dataclass
class SubtitleStreamInfo:
    input_index: int
    language: str | None
    disposition: StreamDisposition
    title: str | None = None
    codec_name: str | None = None


@dataclass
class TranscodeProfile:
    name: str | None
    codec: str = "h264"
    bitrate: str = "8M"
    max_bitrate: str = "8M"
    bufsize: str = "16M"
    level: str = "4.1"
    h264_profile: str = "high"
    max_fps: int = 30
    preset: str = "p6"
    rc_mode: str = "vbr"
    cq: str = "18"
    bframes: int = 2
    lookahead: int = 24
    adaptive_b_frames: bool = True
    aq_enabled: bool = True
    spatial_aq: bool = True
    temporal_aq: bool = True
    aq_strength: int = 7
    resolution: str | None = None
    max_resolution: str | None = None
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_channels: int = 2

    @classmethod
    def from_dict(cls, raw: dict | None, name: str | None = None) -> "TranscodeProfile":
        raw = raw or {}
        audio_cfg = raw.get("audio", {}) or {}

        def _int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        profile = cls(
            name=name,
            codec=str(raw.get("codec") or "h264").lower(),
            bitrate=str(raw.get("bitrate") or raw.get("max_bitrate") or "8M"),
            max_bitrate=str(raw.get("max_bitrate") or raw.get("bitrate") or "8M"),
            bufsize=str(raw.get("bufsize", "16M")),
            level=str(raw.get("level", "4.1")),
            h264_profile=str(raw.get("profile", "high")),
            max_fps=min(CHROMECAST_MAX_FPS, max(1, _int(raw.get("max_fps", 30), 30))),
            preset=str(raw.get("preset", "p6")),
            rc_mode=str(raw.get("rc", "vbr") or "vbr").lower(),
            cq=str(raw.get("cq", 18)),
            bframes=_int(raw.get("bframes", 2), 2),
            lookahead=_int(raw.get("lookahead", 24), 24),
            adaptive_b_frames=bool(raw.get("adaptive_b_frames", True)),
            aq_enabled=bool(raw.get("aq", True)),
            spatial_aq=bool(raw.get("spatial_aq", True)),
            temporal_aq=bool(raw.get("temporal_aq", True)),
            aq_strength=_int(raw.get("aq_strength", 7), 7),
            resolution=raw.get("resolution"),
            max_resolution=raw.get("max_resolution"),
            audio_codec=str(audio_cfg.get("codec", "aac")),
            audio_bitrate=str(audio_cfg.get("bitrate", "192k")),
            audio_channels=_int(audio_cfg.get("channels", 2), 2),
        )
        profile.validate()
        return profile

    def validate(self) -> None:
        errors: list[str] = []
        if self.max_fps <= 0:
            errors.append("max_fps must be >0")
        if self.audio_channels not in (1, 2):
            errors.append("audio_channels must be 1 or 2 for Chromecast")
        if self.aq_strength < 1 or self.aq_strength > 15:
            errors.append("aq_strength must be between 1 and 15")
        if errors:
            raise ValueError(f"Invalid profile {self.name or ''}: {', '.join(errors)}")


class FFmpegBuilder:
    def __init__(
        self,
        analysis: dict,
        input_path: Path,
        output_path: Path,
        profiles: dict,
        ffmpeg_capabilities: FfmpegCapabilities,
        host_environment: dict,
        language_preferences: Iterable[str] | None = None,
    ):
        self.analysis = analysis
        self.input_path = input_path
        self.output_path = output_path
        self.profiles = profiles
        self.ffmpeg_capabilities = ffmpeg_capabilities
        self.host_environment = host_environment
        self.language_preferences = tuple(language_preferences or DEFAULT_LANGUAGE_PREFERENCES)
        self.embed_subtitles = not bool(analysis.get("skip_embedded_subtitles"))

    # --------------- Language helpers --------------- #
    def _normalize_language(self, language: str | None) -> str | None:
        if not language:
            return None
        code = language.lower()
        if code in {"swe", "sv"}:
            return "swe"
        if code in {"eng", "en"}:
            return "eng"
        return code

    # --------------- Profile helpers --------------- #
    def _resolve_profile(self) -> tuple[TranscodeProfile, dict]:
        profile_id = self.analysis.get("profile")
        encoding = self.analysis.get("encoding") or self.profiles.get(profile_id, {})
        pipeline = (
            self.analysis.get("pipeline")
            or (encoding.get("pipeline") if isinstance(encoding, dict) else {})
            or {}
        )
        encode_type = (pipeline.get("encode_type") or "gpu").lower()
        profile_key = "cpu" if encode_type == "cpu" else "gpu"
        raw_profile: dict = {}
        if isinstance(encoding, dict):
            raw_profile = encoding.get(profile_key) or encoding.get("gpu") or encoding
        try:
            profile = TranscodeProfile.from_dict(raw_profile, name=str(profile_id))
        except ValueError as exc:
            LOGGER.warning("Profile %s invalid (%s); using defaults", profile_id, exc)
            profile = TranscodeProfile.from_dict({}, name=str(profile_id))

        if not pipeline:
            pipeline = {
                "decode_type": "gpu",
                "scale_type": "gpu",
                "encode_type": encode_type,
            }
        return profile, pipeline

    # --------------- Stream parsing --------------- #
    def _parse_streams(
        self, streams: list[dict]
    ) -> tuple[list[VideoStreamInfo], list[AudioStreamInfo], list[SubtitleStreamInfo]]:
        videos: list[VideoStreamInfo] = []
        audios: list[AudioStreamInfo] = []
        subs: list[SubtitleStreamInfo] = []
        audio_pos = 0
        subtitle_pos = 0
        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                disposition = stream.get("disposition") or {}
                if disposition.get("attached_pic"):
                    LOGGER.info(
                        "Skipping attached picture stream (codec %s)",
                        stream.get("codec_name"),
                    )
                    continue
                videos.append(
                    VideoStreamInfo(
                        input_index=len(videos),
                        codec_name=stream.get("codec_name"),
                        pix_fmt=stream.get("pix_fmt"),
                        bits_per_raw_sample=stream.get("bits_per_raw_sample"),
                        color_space=stream.get("color_space"),
                        color_transfer=stream.get("color_transfer"),
                        color_primaries=stream.get("color_primaries"),
                        side_data_list=stream.get("side_data_list"),
                        avg_frame_rate=stream.get("avg_frame_rate"),
                        r_frame_rate=stream.get("r_frame_rate"),
                        width=stream.get("width"),
                        height=stream.get("height"),
                        field_order=stream.get("field_order"),
                        bit_rate=stream.get("bit_rate"),
                    )
                )
            elif codec_type == "audio":
                audios.append(
                    AudioStreamInfo(
                        input_index=audio_pos,
                        language=self._normalize_language(stream.get("tags", {}).get("language")),
                        disposition=StreamDisposition.from_raw(stream.get("disposition")),
                        title=stream.get("tags", {}).get("title"),
                        codec_name=stream.get("codec_name"),
                    )
                )
                audio_pos += 1
            elif codec_type == "subtitle":
                subs.append(
                    SubtitleStreamInfo(
                        input_index=subtitle_pos,
                        language=self._normalize_language(stream.get("tags", {}).get("language")),
                        disposition=StreamDisposition.from_raw(stream.get("disposition")),
                        title=stream.get("tags", {}).get("title"),
                        codec_name=stream.get("codec_name"),
                    )
                )
                subtitle_pos += 1
            else:
                LOGGER.warning(
                    "Ignoring unsupported stream type %s (codec %s)",
                    codec_type,
                    stream.get("codec_name"),
                    extra={"stream_type": codec_type, "codec": stream.get("codec_name")},
                )
        return videos, audios, subs

    # --------------- Selection helpers --------------- #
    def _is_commentary(self, stream: AudioStreamInfo | SubtitleStreamInfo) -> bool:
        title = (stream.title or "").lower()
        return bool(stream.disposition.comment or "commentary" in title)

    def _pick_best_stream(self, candidates: Sequence[AudioStreamInfo | SubtitleStreamInfo]):
        if not candidates:
            return None
        original = next((s for s in candidates if s.disposition.original), None)
        if original:
            return original
        default = next((s for s in candidates if s.disposition.default), None)
        if default:
            return default
        return candidates[0]

    def _select_priority_streams(  # noqa: C901
        self, stream_list: Sequence[AudioStreamInfo | SubtitleStreamInfo]
    ) -> tuple[list[AudioStreamInfo | SubtitleStreamInfo], int | None]:
        mapped: list[AudioStreamInfo | SubtitleStreamInfo] = []
        seen_inputs: set[int] = set()
        seen_languages: set[str | None] = set()

        def _infer_original_language() -> str | None:
            original_tag = next((s.language for s in stream_list if s.disposition.original), None)
            if original_tag:
                return original_tag
            non_preferred = [
                s.language
                for s in stream_list
                if s.language not in self.language_preferences and s.language is not None
            ]
            return non_preferred[0] if non_preferred else None

        def _pick_for_language(language: str) -> AudioStreamInfo | SubtitleStreamInfo | None:
            language_matches = [s for s in stream_list if s.language == language]
            if not language_matches:
                return None
            preferred = [s for s in language_matches if not self._is_commentary(s)]
            chosen = self._pick_best_stream(preferred or language_matches)
            if chosen:
                LOGGER.debug(
                    "Selected %s stream for language %s",
                    chosen.__class__.__name__,
                    language,
                    extra={"language": language, "stream_index": chosen.input_index},
                )
            return chosen

        original_language = _infer_original_language()
        language_order = list(self.language_preferences)
        if original_language and original_language not in language_order:
            language_order.append(original_language)

        preferred_streams = [
            _pick_for_language(lang) for lang in language_order if lang is not None
        ]
        original_fallback = self._pick_best_stream(
            [s for s in stream_list if not self._is_commentary(s)] or stream_list
        )

        for candidate in preferred_streams + [original_fallback]:
            if candidate is None:
                continue
            idx = candidate.input_index
            if candidate.language in seen_languages:
                continue
            if idx in seen_inputs:
                continue
            mapped.append(candidate)
            seen_inputs.add(idx)
            seen_languages.add(candidate.language)

        default_idx: int | None = None
        for lang in language_order:
            candidate = next((s for s in mapped if s.language == lang), None)
            if candidate:
                default_idx = mapped.index(candidate)
                break
        if default_idx is None and mapped:
            default_idx = 0
        LOGGER.debug(
            "Selected %s streams (default=%s) with preferences %s",
            len(mapped),
            default_idx,
            self.language_preferences,
            extra={"mapped_streams": len(mapped), "default_idx": default_idx},
        )
        return mapped, default_idx

    def _build_disposition_flags(
        self,
        mapped_streams: list[AudioStreamInfo | SubtitleStreamInfo],
        default_idx: int | None,
        stream_type: str,
    ) -> list[str]:
        flags: list[str] = []
        for output_idx in range(len(mapped_streams)):
            disposition_value = "default" if default_idx == output_idx else "0"
            flags.extend([f"-disposition:{stream_type}:{output_idx}", disposition_value])
        return flags

    # --------------- Filter helpers --------------- #
    def _fps_filter_config(self, source_fps: float | None, max_fps: int) -> tuple[float, bool]:
        if source_fps and source_fps > 0:
            if source_fps > max_fps + 0.01:
                return float(max_fps), True
            return float(source_fps), False
        return float(max_fps), True

    def _build_filter_chain(  # noqa: C901
        self,
        profile: TranscodeProfile,
        video_stream: VideoStreamInfo | None,
        pipeline: dict,
    ) -> tuple[str, bool]:
        """Build the -vf chain. Returns (filter_chain, tonemapped_to_bt709)."""
        decode_type = (pipeline.get("decode_type") or "gpu").lower()
        scale_type = (pipeline.get("scale_type") or "gpu").lower()
        encode_type = (pipeline.get("encode_type") or "gpu").lower()

        source_fps = video_stream.frame_rate() if video_stream else None
        fps_value, needs_fps_filter = self._fps_filter_config(source_fps, profile.max_fps)
        fps_fragment = f"fps={self._format_fps_value(fps_value)}" if needs_fps_filter else ""

        bit_depth = video_stream.bit_depth() if video_stream else None
        is_high_bit_depth = bool(bit_depth and bit_depth > 8)
        requires_tonemap = bool(video_stream and (video_stream.is_hdr() or is_high_bit_depth))
        is_interlaced = video_stream.is_interlaced() if video_stream else False

        target_width, target_height = self._target_dimensions(profile)
        needs_scaling = self._needs_scaling(video_stream, target_width, target_height)

        capabilities = self.ffmpeg_capabilities
        supports_hwaccel = capabilities.supports_hwaccel("cuda")
        supports_hwupload = capabilities.supports_filter("hwupload_cuda")
        gpu_scale_filter = self._gpu_scale_filter(target_width, target_height, video_stream)
        gpu_deinterlace_filter = self._gpu_deinterlace_filter()

        # Tonemapping always runs on the CPU: upstream FFmpeg has no tonemap_cuda
        # (that filter only exists in the jellyfin-ffmpeg fork), and HDR sources are
        # 10-bit which our GPU scale path does not accept anyway.
        use_cpu_tonemap = (
            requires_tonemap
            and capabilities.supports_filter("zscale")
            and capabilities.supports_filter("tonemap")
        )
        if requires_tonemap and not use_cpu_tonemap:
            LOGGER.warning("HDR/10-bit content detected but tonemap filters are unavailable")

        gpu_filtering_possible = (
            decode_type == "gpu"
            and encode_type == "gpu"
            and scale_type == "gpu"
            and supports_hwaccel
            and not is_high_bit_depth
            and not requires_tonemap
            and (not needs_scaling or gpu_scale_filter is not None)
            and (not is_interlaced or gpu_deinterlace_filter is not None)
        )

        filters: list[str] = []

        if gpu_filtering_possible:
            if is_interlaced and gpu_deinterlace_filter:
                filters.append(gpu_deinterlace_filter)
            if needs_scaling and gpu_scale_filter:
                filters.append(gpu_scale_filter)
            if fps_fragment:
                filters.append(fps_fragment)
        elif (
            decode_type == "gpu"
            or encode_type == "gpu"
            or needs_scaling
            or is_interlaced
            or use_cpu_tonemap
            or bool(fps_fragment)
        ):
            if decode_type == "gpu":
                filters.append("hwdownload")
                filters.append("format=p010le" if is_high_bit_depth else "format=yuv420p")
            if is_interlaced:
                cpu_deinterlace_filter = self._cpu_deinterlace_filter()
                if cpu_deinterlace_filter:
                    filters.append(cpu_deinterlace_filter)
                else:
                    LOGGER.warning(
                        "Interlaced content detected but no deinterlace filter is available"
                    )
            if use_cpu_tonemap:
                filters.extend(self._cpu_tonemap_filters())
            # Scale after hwupload only when the pipeline explicitly asks for GPU
            # scaling and the encode also happens on the GPU.
            scale_after_upload = (
                needs_scaling
                and scale_type == "gpu"
                and encode_type == "gpu"
                and gpu_scale_filter is not None
            )
            if needs_scaling and not scale_after_upload:
                filters.append(f"scale={self._scale_dimension_args(target_width, target_height)}")
            if fps_fragment:
                filters.append(fps_fragment)

            if encode_type == "gpu":
                filters.append("format=nv12")
                if supports_hwupload:
                    filters.append("hwupload_cuda")
                if scale_after_upload and gpu_scale_filter:
                    filters.append(gpu_scale_filter)
            else:
                filters.append("format=yuv420p")

        filter_chain = ",".join(filters)
        LOGGER.debug(
            "Video filter chain constructed: %s",
            filter_chain,
            extra={"event": "filter_chain", "filters": filters},
        )
        return filter_chain, use_cpu_tonemap

    def _target_dimensions(self, profile: TranscodeProfile) -> tuple[int, int]:
        hint = profile.max_resolution or profile.resolution
        width, height = DEFAULT_TARGET_RESOLUTION
        if isinstance(hint, str):
            match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", hint)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
            else:
                LOGGER.warning(
                    "Profile %s has malformed resolution %r; using %sx%s",
                    profile.name,
                    hint,
                    width,
                    height,
                )
        if width > CHROMECAST_MAX_WIDTH or height > CHROMECAST_MAX_HEIGHT:
            LOGGER.warning(
                "Profile %s resolution %sx%s exceeds Chromecast limits; clamping to %sx%s",
                profile.name,
                width,
                height,
                CHROMECAST_MAX_WIDTH,
                CHROMECAST_MAX_HEIGHT,
            )
            width = min(width, CHROMECAST_MAX_WIDTH)
            height = min(height, CHROMECAST_MAX_HEIGHT)
        return max(2, width - width % 2), max(2, height - height % 2)

    def _needs_scaling(
        self, video_stream: VideoStreamInfo | None, target_width: int, target_height: int
    ) -> bool:
        if video_stream is None:
            return True
        width, height = video_stream.width, video_stream.height
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            # Unknown dimensions: keep the clamping scale filter in the chain.
            # It never upscales (min() expressions), so it is safe as a no-op.
            return True
        if width % 2 or height % 2:
            # Odd dimensions are not representable in yuv420p/nv12 output.
            return True
        return width > target_width or height > target_height

    def _scale_dimension_args(self, target_width: int, target_height: int) -> str:
        # Clamp both axes to the target box while preserving aspect ratio.
        # min() keeps sources that already fit untouched (never upscale), and
        # force_original_aspect_ratio=decrease shrinks whichever axis overflows.
        return (
            f"w=min(iw\\,{target_width}):h=min(ih\\,{target_height}):"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )

    def _cpu_tonemap_filters(self) -> list[str]:
        return [
            "zscale=t=linear:npl=100",
            "tonemap=hable:desat=0",
            "zscale=p=bt709:t=bt709:m=bt709:r=tv",
        ]

    def _cpu_deinterlace_filter(self) -> str | None:
        # mode=send_frame keeps the source frame rate (one frame per frame);
        # bwdif defaults to send_field which would double the fps.
        for name in ("bwdif", "yadif"):
            if self.ffmpeg_capabilities.supports_filter(name):
                return f"{name}=mode=send_frame"
        return None

    def _gpu_deinterlace_filter(self) -> str | None:
        for name in ("bwdif_cuda", "yadif_cuda"):
            if self.ffmpeg_capabilities.supports_filter(name):
                return f"{name}=mode=send_frame"
        return None

    def _gpu_scale_filter(
        self, target_width: int, target_height: int, video_stream: VideoStreamInfo | None
    ) -> str | None:
        # 10-bit/HDR content is safer to process via CPU (download -> tonemap -> upload)
        # because scale_npp often lacks P010 support and scale_cuda can be flaky.
        if video_stream and (video_stream.bit_depth() or 8) > 8:
            return None

        dimension_args = self._scale_dimension_args(target_width, target_height)
        if self.ffmpeg_capabilities.supports_filter("scale_npp"):
            return f"scale_npp={dimension_args}:format=nv12"
        if self.ffmpeg_capabilities.supports_filter("scale_cuda"):
            return f"scale_cuda={dimension_args}"
        return None

    # --------------- Misc helpers --------------- #
    def _format_fps_value(self, fps: float) -> str:
        if fps.is_integer():
            return str(int(fps))
        return f"{fps:.3f}".rstrip("0").rstrip(".")

    def _ffmpeg_base_command(self, decode_type: str) -> list[str]:
        command = ["ffmpeg", "-y"]
        if decode_type == "gpu":
            command.extend(
                [
                    "-hwaccel",
                    "cuda",
                    "-hwaccel_output_format",
                    "cuda",
                ]
            )
        command.extend(["-i", str(self.input_path)])
        return command

    # --------------- Build --------------- #
    def build(self) -> list[str]:  # noqa: C901
        profile, pipeline = self._resolve_profile()
        decode_type = (pipeline.get("decode_type") or "gpu").lower()
        encode_type = (pipeline.get("encode_type") or "gpu").lower()

        streams = self.analysis.get("streams", []) or []
        video_streams, audio_streams, subtitle_streams = self._parse_streams(streams)
        video_stream = video_streams[0] if video_streams else None

        # Smart Bitrate Clamping. Prefer the video stream's own bitrate; the
        # container bitrate includes audio/subtitles and overshoots slightly.
        source_bitrate = video_stream.video_bit_rate() if video_stream else None
        if source_bitrate is None:
            try:
                source_bitrate = int(self.analysis.get("format", {}).get("bit_rate", 0))
            except (ValueError, TypeError):
                source_bitrate = 0

        if source_bitrate > 0:

            def _parse_bitrate(val: str) -> int:
                if not val:
                    return 0
                s = val.lower().strip()
                mult = 1
                if s.endswith("k"):
                    mult = 1000
                    s = s[:-1]
                elif s.endswith("m"):
                    mult = 1000000
                    s = s[:-1]
                try:
                    return int(float(s) * mult)
                except ValueError:
                    return 0

            target_br = _parse_bitrate(profile.bitrate)
            max_br = _parse_bitrate(profile.max_bitrate)

            # "If source_bitrate * 1.5 < profile_target_bitrate, clamp the output bitrate"
            limit = int(source_bitrate * 1.5)
            if limit < target_br:
                LOGGER.info(
                    "Smart bitrate clamping: reducing target %s -> %s (source %s)",
                    profile.bitrate,
                    limit,
                    source_bitrate,
                )
                profile.bitrate = str(limit)
                # "Ensure it never exceeds the profile's max_bitrate"
                # If our new target is lower, we satisfy max_bitrate
                # (assuming max was >= old target).
                # If max_bitrate is somehow lower than limit (unlikely for a profile),
                # we should respect max.
                if max_br > 0 and limit > max_br:
                    profile.bitrate = str(max_br)

        # Robust Subtitle Handling
        valid_subs = []
        for sub in subtitle_streams:
            codec = (sub.codec_name or "").lower()
            if codec in SUPPORTED_SUBTITLE_CODECS:
                valid_subs.append(sub)
            else:
                LOGGER.warning(
                    "Skipping unsupported bitmap subtitle stream %s (codec %s)",
                    sub.input_index,
                    codec,
                )
        subtitle_streams = valid_subs

        selected_audio, default_audio_idx = self._select_priority_streams(audio_streams)
        selected_subtitles, default_sub_idx = self._select_priority_streams(subtitle_streams)
        if not self.embed_subtitles:
            LOGGER.info("Skipping embedded subtitles (extraction flagged) for %s", self.input_path)
            selected_subtitles = []
            default_sub_idx = None

        # 1. Base Command (Input)
        command: list[str] = self._ffmpeg_base_command(decode_type)

        # 2. Main Output Options
        main_options: list[str] = []
        if video_streams:
            # 0:V excludes attached pictures (cover art); Chromecast expects a
            # single video track, so map only the first real video stream.
            main_options.extend(["-map", "0:V:0"])
        for audio_stream in selected_audio:
            main_options.extend(["-map", f"0:a:{audio_stream.input_index}"])
        for subtitle_stream in selected_subtitles:
            main_options.extend(["-map", f"0:s:{subtitle_stream.input_index}"])

        audio_dispositions = self._build_disposition_flags(selected_audio, default_audio_idx, "a")
        subtitle_dispositions = self._build_disposition_flags(
            selected_subtitles, default_sub_idx, "s"
        )

        video_filter, tonemapped = self._build_filter_chain(profile, video_stream, pipeline)

        if video_filter:
            main_options.extend(["-vf", video_filter])

        h264_profile_lower = profile.h264_profile.lower()
        bframes = profile.bframes if h264_profile_lower != "baseline" else 0

        if encode_type == "gpu":
            encoder_name = self.ffmpeg_capabilities.preferred_encoder_for_codec(profile.codec)
            main_options.extend(
                [
                    "-c:v",
                    encoder_name,
                    "-preset",
                    profile.preset,
                    "-profile:v",
                    profile.h264_profile,
                    "-level",
                    profile.level,
                ]
            )
            # main_options.extend(["-pix_fmt", "nv12"])
            rc_mode = profile.rc_mode
            if rc_mode == "cbr":
                rc_mode = "vbr"
            encoder_cap: EncoderCapabilities | None = self.ffmpeg_capabilities.encoder_capability(
                encoder_name
            )
            if encoder_cap and not encoder_cap.supports_rc(rc_mode):
                LOGGER.warning(
                    "Requested rc mode %s unavailable for %s; falling back to vbr (WSL2=%s)",
                    rc_mode,
                    encoder_name,
                    self.host_environment.get("is_wsl2"),
                )
                rc_mode = "vbr"
            multipass_mode: str | None = None
            if rc_mode == "vbr_hq":
                multipass_mode = "fullres"
            if (
                multipass_mode
                and encoder_cap
                and not encoder_cap.supports_multipass(multipass_mode)
            ):
                LOGGER.warning(
                    "NVENC multipass %s is unavailable for %s; continuing without multipass",
                    multipass_mode,
                    encoder_name,
                )
                multipass_mode = None

            lookahead = profile.lookahead
            if encoder_cap and not encoder_cap.supports_lookahead and lookahead > 0:
                LOGGER.warning(
                    "NVENC encoder %s lacks rc-lookahead support; forcing lookahead=0",
                    encoder_name,
                )
                lookahead = 0

            adaptive_b_frames = profile.adaptive_b_frames and lookahead > 0 and bframes > 0
            if h264_profile_lower == "baseline":
                adaptive_b_frames = False
            aq_enabled = profile.aq_enabled
            spatial_aq = aq_enabled and profile.spatial_aq
            temporal_aq = aq_enabled and profile.temporal_aq
            aq_strength = max(1, min(15, profile.aq_strength))

            if rc_mode == "cq":
                main_options.extend(["-rc", "constqp", "-qp", profile.cq])
            else:
                main_options.extend(
                    [
                        "-rc",
                        rc_mode,
                        "-b:v",
                        profile.bitrate,
                        "-maxrate",
                        profile.max_bitrate,
                        "-bufsize",
                        profile.bufsize,
                    ]
                )
                if multipass_mode:
                    main_options.extend(["-multipass", multipass_mode])

            main_options.extend(["-bf", str(bframes)])

            main_options.extend(["-rc-lookahead", str(lookahead)])
            main_options.extend(["-b_adapt", "1" if adaptive_b_frames else "0"])
            main_options.extend(
                [
                    "-spatial_aq",
                    "1" if spatial_aq else "0",
                    "-temporal_aq",
                    "1" if temporal_aq else "0",
                ]
            )
            if aq_enabled:
                main_options.extend(["-aq-strength", str(aq_strength)])
        else:
            main_options.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    profile.preset,
                    "-profile:v",
                    profile.h264_profile,
                    "-level",
                    profile.level,
                    "-pix_fmt",
                    "yuv420p",
                ]
            )
            if profile.rc_mode == "crf":
                main_options.extend(["-crf", str(profile.cq)])
            else:
                main_options.extend(
                    [
                        "-b:v",
                        profile.bitrate,
                        "-maxrate",
                        profile.max_bitrate,
                        "-bufsize",
                        profile.bufsize,
                    ]
                )
            main_options.extend(["-bf", str(bframes)])

        if tonemapped:
            # The zscale/tonemap chain converts to BT.709; tag the output so
            # players do not misinterpret the stream as BT.2020/PQ.
            main_options.extend(
                [
                    "-colorspace:v",
                    "bt709",
                    "-color_primaries:v",
                    "bt709",
                    "-color_trc:v",
                    "bt709",
                ]
            )
        main_options.extend(["-movflags", "+faststart"])

        if selected_audio:
            main_options.extend(
                [
                    "-c:a",
                    profile.audio_codec,
                    "-b:a",
                    profile.audio_bitrate,
                    "-ac",
                    str(profile.audio_channels),
                ]
            )

        if selected_subtitles:
            main_options.extend(["-c:s", "mov_text"])

        main_options.extend(audio_dispositions)
        main_options.extend(subtitle_dispositions)

        # 3. Global Options (Progress)
        command.extend(["-progress", "pipe:1"])

        # 4. Assemble Command
        # Add main output options + main output path
        command.extend(main_options)
        command.append(str(self.output_path))

        return command
