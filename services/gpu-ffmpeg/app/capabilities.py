from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence, Set

LOGGER = logging.getLogger("gpu-ffmpeg.capabilities")


@dataclass(frozen=True)
class EncoderCapabilities:
    name: str
    rc_modes: frozenset[str] = frozenset()
    multipass_modes: frozenset[str] = frozenset()
    supports_lookahead: bool = False

    def supports_rc(self, mode: str | None) -> bool:
        if not mode:
            return True
        return mode.lower() in self.rc_modes

    def supports_multipass(self, mode: str | None) -> bool:
        if not mode:
            return True
        return mode.lower() in self.multipass_modes


class FfmpegCapabilities:
    _ENCODERS_TO_INSPECT: Sequence[str] = ("h264_nvenc", "hevc_nvenc", "av1_nvenc")
    _PREFERRED_ENCODERS: Mapping[str, Sequence[str]] = {
        "h264": ("h264_nvenc",),
        "hevc": ("hevc_nvenc", "h264_nvenc"),
        "av1": ("av1_nvenc", "h264_nvenc"),
    }

    def __init__(
        self,
        ffmpeg_executable: str = "ffmpeg",
        *,
        skip_detection: bool = False,
        filters: Iterable[str] | None = None,
        encoders: Iterable[str] | None = None,
        decoders: Iterable[str] | None = None,
        hwaccels: Iterable[str] | None = None,
        encoder_capabilities: Mapping[str, EncoderCapabilities] | None = None,
    ):
        self._executable = ffmpeg_executable
        if skip_detection:
            self.filters = {entry.lower() for entry in (filters or [])}
            self.encoders = {entry.lower() for entry in (encoders or [])}
            self.decoders = {entry.lower() for entry in (decoders or [])}
            self.hwaccels = {entry.lower() for entry in (hwaccels or [])}
            self.encoder_capabilities = {
                name.lower(): entry for name, entry in (encoder_capabilities or {}).items()
            }
        else:
            self.refresh()

    def refresh(self) -> None:
        self.filters = self._probe_features(["-filters"])
        self.encoders = self._probe_features(["-encoders"])
        self.decoders = self._probe_features(["-decoders"])
        self.hwaccels = self._probe_hwaccels()
        self.encoder_capabilities = {}
        for name in self._ENCODERS_TO_INSPECT:
            if name in self.encoders:
                self.encoder_capabilities[name] = self._inspect_encoder(name)
        LOGGER.info(
            "Discovered FFmpeg capabilities (filters=%s, encoders=%s, hwaccels=%s)",
            len(self.filters),
            len(self.encoders),
            len(self.hwaccels),
        )

    def _run(self, arguments: list[str]) -> str:
        command = [self._executable, "-hide_banner", "-loglevel", "quiet"] + arguments
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except FileNotFoundError as exc:  # pragma: no cover - defensive
            LOGGER.warning("FFmpeg not found (%s)", exc)
        except subprocess.SubprocessError as exc:
            LOGGER.warning("FFmpeg command failed (%s): %s", " ".join(arguments), exc)
        return ""

    def _probe_features(self, arguments: list[str]) -> set[str]:
        output = self._run(arguments)
        features: set[str] = set()
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            features.add(parts[1].lower())
        return features

    def _probe_hwaccels(self) -> set[str]:
        output = self._run(["-hwaccels"])
        methods: set[str] = set()
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("hardware"):
                continue
            methods.add(stripped.split()[0].lower())
        return methods

    def _inspect_encoder(self, name: str) -> EncoderCapabilities:
        output = self._run(["-h", f"encoder={name}"])
        if not output:
            return EncoderCapabilities(name=name)
        lower_output = output.lower()
        rc_modes = self._choices_from_help(lower_output, "rc")
        multipass_modes = self._choices_from_help(lower_output, "multipass")
        return EncoderCapabilities(
            name=name,
            rc_modes=frozenset(rc_modes),
            multipass_modes=frozenset(multipass_modes),
            supports_lookahead="rc-lookahead" in lower_output,
        )

    def _choices_from_help(self, help_text: str, option: str) -> Set[str]:
        matches = re.findall(rf"-{option}\b[^\n]*choices:?\s*([\w_, ]+)", help_text, re.IGNORECASE)
        choices: set[str] = set()
        for match in matches:
            for segment in match.split(","):
                cleaned = segment.strip()
                cleaned = cleaned.split(" ")[0]
                if cleaned:
                    choices.add(cleaned.lower())
        return choices

    def supports_filter(self, name: str) -> bool:
        return name.lower() in self.filters

    def supports_hwaccel(self, name: str) -> bool:
        return name.lower() in self.hwaccels

    def has_encoder(self, name: str) -> bool:
        return name.lower() in self.encoders

    def encoder_capability(self, name: str) -> Optional[EncoderCapabilities]:
        return self.encoder_capabilities.get(name.lower())

    def preferred_encoder_for_codec(self, codec: str) -> str:
        normalized = (codec or "").lower()
        for candidate in self._PREFERRED_ENCODERS.get(normalized, self._PREFERRED_ENCODERS["h264"]):
            if self.has_encoder(candidate):
                return candidate
        return self._PREFERRED_ENCODERS["h264"][0]
