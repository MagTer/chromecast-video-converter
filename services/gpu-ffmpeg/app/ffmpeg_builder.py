import logging
from fractions import Fraction
from pathlib import Path

LOGGER = logging.getLogger("gpu-ffmpeg.builder")

SCALING_EXPRESSION = "scale_cuda=-2:720:force_original_aspect_ratio=decrease"


class FFmpegBuilder:
    def __init__(
        self,
        analysis: dict,
        input_path: Path,
        output_path: Path,
        profiles: dict,
        nvenc_capabilities: dict,
        host_environment: dict,
    ):
        self.analysis = analysis
        self.input_path = input_path
        self.output_path = output_path
        self.profiles = profiles
        self.nvenc_capabilities = nvenc_capabilities
        self.host_environment = host_environment

    def _normalize_language(self, language: str | None) -> str | None:
        if not language:
            return None
        code = language.lower()
        if code in {"swe", "sv"}:
            return "swe"
        if code in {"eng", "en"}:
            return "eng"
        return code

    def _scaling_expression(self, profile: dict) -> str:
        resolution = profile.get("max_resolution") or profile.get("resolution")
        if resolution:
            try:
                _, height_str = resolution.lower().split("x", 1)
                height = int(height_str)
                if height > 0:
                    return f"scale_cuda=-2:{height}:force_original_aspect_ratio=decrease"
            except (ValueError, AttributeError):
                LOGGER.debug("Invalid resolution %s; falling back to default scale", resolution)
        return SCALING_EXPRESSION

    def _parse_frame_rate(self, value: str | int | float | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value) if value > 0 else None
        try:
            if "/" in value:
                return float(Fraction(value))
            return float(value)
        except (ValueError, ZeroDivisionError, TypeError):
            return None

    def _source_frame_rate(self) -> float | None:
        streams = self.analysis.get("streams") or []
        for stream in streams:
            if stream.get("codec_type") != "video":
                continue
            fps = self._parse_frame_rate(stream.get("avg_frame_rate"))
            if fps:
                return fps
            fps = self._parse_frame_rate(stream.get("r_frame_rate"))
            if fps:
                return fps
        return None

    def _format_fps_value(self, fps: float) -> str:
        if fps.is_integer():
            return str(int(fps))
        return f"{fps:.3f}".rstrip("0").rstrip(".")

    def _fps_filter_config(self, max_fps: int) -> tuple[float, bool]:
        source_fps = self._source_frame_rate()
        if source_fps and source_fps > 0:
            if source_fps > max_fps + 0.01:
                return float(max_fps), True
            return float(source_fps), False
        return float(max_fps), True

    def _gather_streams(self, streams: list[dict]) -> tuple[bool, list[dict], list[dict]]:
        video_present = False
        audio_streams: list[dict] = []
        subtitle_streams: list[dict] = []
        audio_pos = 0
        subtitle_pos = 0
        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                video_present = True
            elif codec_type == "audio":
                audio_streams.append(
                    {
                        "input_index": audio_pos,
                        "language": self._normalize_language(
                            stream.get("tags", {}).get("language")
                        ),
                        "disposition": stream.get("disposition", {}),
                        "title": stream.get("tags", {}).get("title"),
                    }
                )
                audio_pos += 1
            elif codec_type == "subtitle":
                subtitle_streams.append(
                    {
                        "input_index": subtitle_pos,
                        "language": self._normalize_language(
                            stream.get("tags", {}).get("language")
                        ),
                        "disposition": stream.get("disposition", {}),
                        "title": stream.get("tags", {}).get("title"),
                    }
                )
                subtitle_pos += 1
        return video_present, audio_streams, subtitle_streams

    def _is_commentary(self, stream: dict) -> bool:
        disposition = stream.get("disposition", {}) or {}
        title = (stream.get("title") or "").lower()
        return bool(disposition.get("comment") or "commentary" in title)

    def _pick_best_stream(self, candidates: list[dict]) -> dict | None:
        if not candidates:
            return None
        original = next((s for s in candidates if s.get("disposition", {}).get("original")), None)
        if original:
            return original
        default = next((s for s in candidates if s.get("disposition", {}).get("default")), None)
        if default:
            return default
        return candidates[0]

    def _select_priority_streams(self, stream_list: list[dict]) -> tuple[list[dict], int | None]:
        mapped: list[dict] = []
        seen_inputs: set[int] = set()

        def _pick_for_language(language: str) -> dict | None:
            language_matches = [s for s in stream_list if s.get("language") == language]
            if not language_matches:
                return None
            preferred = [s for s in language_matches if not self._is_commentary(s)]
            return self._pick_best_stream(preferred or language_matches)

        swedish = _pick_for_language("swe")
        english = _pick_for_language("eng")

        non_commentary = [s for s in stream_list if not self._is_commentary(s)]
        original_fallback = self._pick_best_stream(non_commentary or stream_list)

        for candidate in [swedish, english, original_fallback]:
            if candidate is None:
                continue
            idx = candidate["input_index"]
            if idx in seen_inputs:
                continue
            mapped.append(candidate)
            seen_inputs.add(idx)

        default_idx: int | None = None
        if swedish is not None and swedish in mapped:
            default_idx = mapped.index(swedish)
        elif english is not None and english in mapped:
            default_idx = mapped.index(english)
        elif mapped:
            default_idx = 0
        return mapped, default_idx

    def _build_disposition_flags(
        self, mapped_streams: list[dict], default_idx: int | None, stream_type: str
    ) -> list[str]:
        flags: list[str] = []
        for output_idx in range(len(mapped_streams)):
            disposition_value = "default" if default_idx == output_idx else "0"
            flags.extend([f"-disposition:{stream_type}:{output_idx}", disposition_value])
        return flags

    def _ffmpeg_base_command(self) -> list[str]:
        return [
            "ffmpeg",
            "-y",
            "-hwaccel",
            "cuda",
            "-hwaccel_output_format",
            "cuda",
            "-i",
            str(self.input_path),
        ]

    def build(self) -> list[str]:  # noqa: C901
        profile = self.analysis.get("encoding") or self.profiles.get(
            self.analysis.get("profile"),
            {},
        )

        profile = profile or {}
        bitrate = profile.get("bitrate") or profile.get("max_bitrate", "8M")
        maxrate = profile.get("max_bitrate", bitrate)
        bufsize = profile.get("bufsize", "16M")
        level = profile.get("level", "4.1")
        h264_profile = str(profile.get("profile", "high"))
        h264_profile_lower = h264_profile.lower()
        max_fps = max(1, int(profile.get("max_fps", 30) or 30))
        preset = str(profile.get("preset", "p6"))
        rc_mode = str(profile.get("rc", "vbr") or "vbr").lower()
        if rc_mode == "cbr":
            rc_mode = "vbr"
        cq = str(profile.get("cq", 18))
        bframes = int(profile.get("bframes", 2) or 0)
        if h264_profile_lower == "baseline":
            bframes = 0
        lookahead = int(profile.get("lookahead", 24) or 0)
        adaptive_b_frames = bool(profile.get("adaptive_b_frames", True))
        adaptive_b_frames = adaptive_b_frames and lookahead > 0 and bframes > 0
        if h264_profile_lower == "baseline":
            adaptive_b_frames = False
        aq_enabled = bool(profile.get("aq", True))
        spatial_aq = aq_enabled and bool(profile.get("spatial_aq", True))
        temporal_aq = aq_enabled and bool(profile.get("temporal_aq", True))
        aq_strength = int(profile.get("aq_strength", 7) or 7)
        aq_strength = max(1, min(15, aq_strength))
        multipass_mode: str | None = None
        if rc_mode == "vbr_hq":
            multipass_mode = "fullres"

        if rc_mode == "vbr_hq" and not self.nvenc_capabilities.get("rc_vbr_hq", True):
            LOGGER.warning(
                "Requested rc mode vbr_hq is unavailable; falling back to vbr (WSL2=%s)",
                self.host_environment.get("is_wsl2"),
            )
            rc_mode = "vbr"
            multipass_mode = None

        if multipass_mode and not self.nvenc_capabilities.get("multipass_fullres", True):
            LOGGER.warning(
                "NVENC multipass fullres mode is unavailable; continuing without multipass",
            )
            multipass_mode = None
        audio_cfg = profile.get("audio", {})
        audio_codec = audio_cfg.get("codec", "aac")
        audio_bitrate = audio_cfg.get("bitrate", "192k")
        audio_channels = 2  # Chromecast-safe stereo only

        streams = self.analysis.get("streams", [])
        video_present, audio_streams, subtitle_streams = self._gather_streams(streams)

        selected_audio, default_audio_idx = self._select_priority_streams(audio_streams)
        selected_subtitles, default_sub_idx = self._select_priority_streams(subtitle_streams)

        command: list[str] = self._ffmpeg_base_command()

        if video_present:
            command.extend(["-map", "0:v"])

        for audio_stream in selected_audio:
            command.extend(["-map", f"0:a:{audio_stream['input_index']}"])

        for subtitle_stream in selected_subtitles:
            command.extend(["-map", f"0:s:{subtitle_stream['input_index']}"])

        audio_dispositions = self._build_disposition_flags(selected_audio, default_audio_idx, "a")
        subtitle_dispositions = self._build_disposition_flags(
            selected_subtitles, default_sub_idx, "s"
        )

        filters = [self._scaling_expression(profile)]
        if max_fps > 0:
            fps_value, needs_filter = self._fps_filter_config(max_fps)
            if needs_filter:
                filters.append(f"fps={self._format_fps_value(fps_value)}")
        video_filter = ",".join(filters + ["hwdownload", "format=nv12", "hwupload_cuda"])

        command.extend(
            [
                "-vf",
                video_filter,
                "-c:v",
                "h264_nvenc",
                "-preset",
                preset,
                "-profile:v",
                h264_profile,
                "-level",
                level,
            ]
        )
        command.extend(["-pix_fmt", "nv12"])

        if rc_mode == "cq":
            command.extend(["-rc", "constqp", "-qp", cq])
        else:
            command.extend(
                ["-rc", rc_mode, "-b:v", bitrate, "-maxrate", maxrate, "-bufsize", bufsize]
            )
            if multipass_mode:
                command.extend(["-multipass", multipass_mode])

        command.extend(["-bf", str(bframes)])

        if lookahead > 0:
            command.extend(["-rc-lookahead", str(lookahead)])
        else:
            command.extend(["-rc-lookahead", "0"])

        command.extend(["-b_adapt", "1" if adaptive_b_frames else "0"])
        command.extend(
            [
                "-spatial_aq",
                "1" if spatial_aq else "0",
                "-temporal_aq",
                "1" if temporal_aq else "0",
            ]
        )
        if aq_enabled:
            command.extend(["-aq-strength", str(aq_strength)])
        command.extend(["-movflags", "+faststart"])

        if selected_audio:
            command.extend(
                [
                    "-c:a",
                    audio_codec,
                    "-b:a",
                    audio_bitrate,
                    "-ac",
                    str(audio_channels),
                ]
            )

        if selected_subtitles:
            command.extend(["-c:s", "mov_text"])

        command.extend(audio_dispositions)
        command.extend(subtitle_dispositions)

        command.extend(["-progress", "pipe:1", str(self.output_path)])

        return command
