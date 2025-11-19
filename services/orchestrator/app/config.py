from __future__ import annotations

import errno
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

LOGGER = logging.getLogger("orchestrator.config")


def _validate_codecs(codec: str, audio_codec: str) -> None:
    if codec.lower() != "h264":
        raise ValueError("Only H.264 is supported to keep Chromecast compatibility.")
    if audio_codec.lower() != "aac":
        raise ValueError("Audio codec must be AAC for Chromecast.")


def _validate_profile(profile: str, level: str) -> None:
    allowed_profiles = {"baseline", "main", "high"}
    if profile.lower() not in allowed_profiles:
        raise ValueError("Chromecast Gen 2 only supports H.264 baseline, main, or high profiles.")

    try:
        level_value = float(level)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("Video level must be numeric (e.g. 4.1).") from exc
    if level_value > 4.1:
        raise ValueError("Chromecast Gen 2 supports up to level 4.1 for H.264.")


def _validate_resolution(resolution: str) -> None:
    try:
        width_str, height_str = resolution.lower().split("x", 1)
        width, height = int(width_str), int(height_str)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("Resolution must be formatted as WIDTHxHEIGHT.") from exc
    if width > 1920 or height > 1080:
        raise ValueError("Resolution must not exceed 1920x1080 for Chromecast Gen 2.")


def _bitrate_to_int(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.endswith("k"):
        return int(float(normalized[:-1]) * 1_000)
    if normalized.endswith("m"):
        return int(float(normalized[:-1]) * 1_000_000)
    return int(float(normalized))


def _validate_bitrates(max_bitrate: str, bufsize: str, audio_bitrate: str) -> None:
    try:
        maxrate = _bitrate_to_int(max_bitrate)
        bufsize_value = _bitrate_to_int(bufsize)
        audio_rate = _bitrate_to_int(audio_bitrate)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("Bitrate values must be numeric and end with 'k' or 'M'.") from exc

    if maxrate > 12_000_000:
        raise ValueError("Chromecast Gen 2 cannot exceed ~12 Mbps video bitrate.")
    if bufsize_value > 24_000_000:
        raise ValueError("Buffer size must remain within Chromecast Gen 2 decoder limits.")
    if audio_rate > 512_000:
        raise ValueError("Audio bitrate must remain below 512 kbps for Chromecast Gen 2.")


def _validate_encoding_options(
    preset: str, cq: int, rc_mode: str, max_fps: int, audio_channels: int
) -> None:
    allowed_presets = {"p1", "p2", "p3", "p4", "p5", "p6", "p7"}
    if preset.lower() not in allowed_presets:
        raise ValueError("NVENC preset must be one of p1–p7 for Chromecast-safe outputs.")

    if cq < 0 or cq > 30:
        raise ValueError(
            "NVENC CQ must be between 0 and 30 for stable quality on Gen 2 Chromecasts."
        )

    allowed_rc_modes = {"vbr_hq", "vbr", "cbr"}
    if rc_mode.lower() not in allowed_rc_modes:
        raise ValueError(
            "Rate control must be one of vbr_hq, vbr, or cbr for Chromecast-safe outputs."
        )

    if max_fps <= 0 or max_fps > 30:
        raise ValueError("Frame rate must not exceed 30 fps for Chromecast Gen 2 compatibility.")

    if audio_channels != 2:
        raise ValueError("Audio must remain stereo (2 channels) for Chromecast Gen 2.")


class AudioProfile(BaseModel):
    codec: str
    bitrate: str
    channels: int = Field(default=2, ge=1, le=8)

    @model_validator(mode="after")
    def validate_channels(self) -> "AudioProfile":
        if self.channels != 2:
            raise ValueError("Chromecast Gen 2 supports stereo output; enforce 2 channels.")
        return self


class Profile(BaseModel):
    codec: str
    profile: str
    level: str
    resolution: str
    max_fps: int = Field(default=30, gt=0, le=30)
    max_bitrate: str
    bufsize: str
    preset: str = Field(default="p5")
    cq: int = Field(default=18, ge=0, le=30)
    rc: str = Field(default="vbr_hq")
    audio: AudioProfile

    @model_validator(mode="after")
    def validate_codecs(cls, values):
        _validate_codecs(values.codec, values.audio.codec)
        _validate_profile(values.profile, values.level)
        _validate_resolution(values.resolution)
        _validate_bitrates(values.max_bitrate, values.bufsize, values.audio.bitrate)
        _validate_encoding_options(
            values.preset, values.cq, values.rc, values.max_fps, values.audio.channels
        )
        return values


class LibraryConfig(BaseModel):
    root: str
    depth: str
    profile: str


class OperationConfig(BaseModel):
    max_concurrent_jobs: int
    gpu_temperature_cutoff: int
    max_disk_usage_percent: int
    remove_original_after_success: bool = False


class JellyfinConfig(BaseModel):
    url: str
    api_key: str
    libraries: Dict[str, int]


class LoggingConfig(BaseModel):
    retention_days: int = Field(default=7, ge=1, le=90)


class QualityConfig(BaseModel):
    libraries: Dict[str, LibraryConfig]
    profiles: Dict[str, Profile]
    operational: OperationConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    notifiers: Dict[str, dict] = Field(default_factory=dict)
    jellyfin: Optional[JellyfinConfig] = None

    def profile_for(self, library_name: str) -> Profile:
        library = self.libraries[library_name]
        profile_name = library.profile
        if profile_name not in self.profiles:
            raise ValueError(f"Profile {profile_name} is not defined.")
        return self.profiles[profile_name]

    def profile_named(self, profile_name: str) -> Profile:
        if profile_name not in self.profiles:
            raise ValueError(f"Profile {profile_name} is not defined.")
        return self.profiles[profile_name]


@dataclass
class ConfigSource:
    path: Path
    config: QualityConfig


def update_profile(config_source: ConfigSource, name: str, data: dict) -> Profile:
    profile = Profile(**data)
    config_source.config.profiles[name] = profile
    LOGGER.info("Updated encoding profile '%s' for Chromecast-safe settings.", name)
    persist_config(config_source)
    return profile


def load_config(path: Path) -> ConfigSource:
    if not path.exists():
        raise FileNotFoundError(f"Quality config not found at {path}")
    raw = yaml.safe_load(path.read_text())
    try:
        config = QualityConfig(**raw)
    except ValidationError as exc:
        LOGGER.error("Failed to validate quality configuration: %s", exc)
        raise

    LOGGER.info(
        "Loaded quality config (%s libraries, %s profiles)",
        len(config.libraries),
        len(config.profiles),
    )
    LOGGER.debug(json.dumps(raw, indent=2))
    return ConfigSource(path=path, config=config)


def persist_config(source: ConfigSource) -> None:
    payload = source.config.model_dump()
    try:
        serialized = yaml.safe_dump(payload, sort_keys=False)
        source.path.write_text(serialized, encoding="utf-8")
        LOGGER.info("Persisted settings to %s", source.path)
    except OSError as exc:
        if exc.errno == errno.EROFS:
            LOGGER.warning(
                "Config path %s is read-only; keeping updates in memory only (%s)",
                source.path,
                exc,
            )
            return
        LOGGER.error("Failed to persist settings to %s: %s", source.path, exc)
        raise
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Failed to persist settings to %s: %s", source.path, exc)
        raise
