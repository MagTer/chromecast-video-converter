from __future__ import annotations

import copy
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

LOGGER = logging.getLogger("orchestrator.config")


DEFAULT_CONFIG: Dict[str, Any] = {
    "libraries": {
        "movies": {
            "root": "/watch/movies",
            "depth": "max",
            "profile": "chromecast",
            "profile_id": 1,
        },
        "series": {
            "root": "/watch/series",
            "depth": "max",
            "profile": "chromecast",
            "profile_id": 1,
        },
    },
    "profiles": {
        "chromecast": {
            "gpu": {
                "mode": "gpu",
                "codec": "h264",
                "profile": "high",
                "level": "4.0",
                "resolution": "1280x720",
                "max_fps": 30,
                "bitrate": "5M",
                "max_bitrate": "10M",
                "bufsize": "16M",
                "preset": "p7",
                "rc": "vbr_hq",
                "cq": 18,
                "bframes": 2,
                "lookahead": 24,
                "adaptive_b_frames": True,
                "aq": True,
                "spatial_aq": True,
                "temporal_aq": True,
                "aq_strength": 7,
                "audio": {
                    "codec": "aac",
                    "bitrate": "192k",
                    "channels": 2,
                },
            },
            "cpu": {
                "mode": "cpu",
                "codec": "h264",
                "profile": "high",
                "level": "4.1",
                "resolution": "1920x1080",
                "max_fps": 30,
                "bitrate": "5M",
                "max_bitrate": "8M",
                "bufsize": "16M",
                "preset": "veryslow",
                "rc": "crf",
                "cq": 18,
                "bframes": 3,
                "lookahead": 60,
                "adaptive_b_frames": True,
                "aq": True,
                "spatial_aq": True,
                "temporal_aq": True,
                "aq_strength": 7,
                "audio": {
                    "codec": "aac",
                    "bitrate": "192k",
                    "channels": 2,
                },
            },
        }
    },
    "operational": {
        "max_concurrent_jobs": 1,
        "gpu_temperature_cutoff": 85,
        "max_disk_usage_percent": 90,
        "remove_original_after_success": False,
    },
    "logging": {"retention_days": 7},
    "notifiers": {
        "webhook": {
            "url": "https://hooks.example.com/notify",
        }
    },
}


def _validate_codecs(codec: str, audio_codec: str) -> None:
    """Enforce GPU-friendly H.264 + AAC outputs only."""

    if codec.lower() != "h264":
        raise ValueError("Only H.264 is supported to keep Chromecast compatibility.")
    if audio_codec.lower() != "aac":
        raise ValueError("Audio codec must be AAC for Chromecast.")


def _parse_resolution(resolution: str) -> tuple[int, int]:
    try:
        width_str, height_str = resolution.lower().split("x", 1)
        width, height = int(width_str), int(height_str)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("Resolution must be formatted as WIDTHxHEIGHT.") from exc
    return width, height


def _minimum_level_for_resolution(resolution: str, fps: int) -> float:
    """Return the lowest H.264 level that can carry the selected resolution/FPS.

    The mapping follows common decoder limits (FFmpeg 7.7 + NVENC constraints):
    - 720p30 => 3.1
    - 720p60 => 4.0
    - 1080p30 => 4.1
    - 1080p60 => 4.2
    Anything beyond 1080p60 is rejected upstream.
    """

    width, height = _parse_resolution(resolution)
    if width > 1920 or height > 1080:
        raise ValueError("Resolution must not exceed 1920x1080 for Chromecast targets.")

    if width <= 1280 and height <= 720:
        if fps <= 30:
            return 3.1
        if fps <= 60:
            return 4.0
    if width <= 1920 and height <= 1080:
        if fps <= 30:
            return 4.1
        if fps <= 60:
            return 4.2
    raise ValueError("Unsupported resolution/FPS combination.")


def _validate_profile(profile: str, level: str, resolution: str, fps: int) -> None:
    allowed_profiles = {"baseline", "main", "high"}
    if profile.lower() not in allowed_profiles:
        raise ValueError("Chromecast Gen 2 only supports H.264 baseline, main, or high profiles.")

    try:
        level_value = float(level)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("Video level must be numeric (e.g. 4.1).") from exc

    minimum_level = _minimum_level_for_resolution(resolution, fps)
    if level_value < minimum_level:
        raise ValueError(
            f"Level {level} is too low for {resolution} at {fps} fps; "
            f"minimum is {minimum_level:.1f}."
        )


def _validate_resolution(resolution: str) -> None:
    width, height = _parse_resolution(resolution)
    if width > 1920 or height > 1080:
        raise ValueError("Resolution must not exceed 1920x1080 for Chromecast Gen 2.")


def _bitrate_to_int(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.endswith("k"):
        return int(float(normalized[:-1]) * 1_000)
    if normalized.endswith("m"):
        return int(float(normalized[:-1]) * 1_000_000)
    return int(float(normalized))


def _validate_bitrates(bitrate: str, max_bitrate: str, bufsize: str, audio_bitrate: str) -> None:
    try:
        target_rate = _bitrate_to_int(bitrate)
        maxrate = _bitrate_to_int(max_bitrate)
        bufsize_value = _bitrate_to_int(bufsize)
        audio_rate = _bitrate_to_int(audio_bitrate)
    except ValueError as exc:  # noqa: BLE001
        raise ValueError("Bitrate values must be numeric and end with 'k' or 'M'.") from exc

    if target_rate <= 0:
        raise ValueError("Bitrate must be greater than zero for VBR modes.")

    if maxrate > 12_000_000:
        raise ValueError("Chromecast Gen 2 cannot exceed ~12 Mbps video bitrate.")
    if bufsize_value > 24_000_000:
        raise ValueError("Buffer size must remain within Chromecast Gen 2 decoder limits.")
    if audio_rate > 512_000:
        raise ValueError("Audio bitrate must remain below 512 kbps for Chromecast Gen 2.")

    if target_rate > maxrate:
        raise ValueError("Target bitrate must not exceed the configured maxrate.")


def _sanitize_profiles(raw: dict) -> dict:  # noqa: C901
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        return raw

    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        for variant_key in ("gpu", "cpu"):
            variant = profile.get(variant_key)
            if not isinstance(variant, dict):
                continue
            bitrate = variant.get("bitrate")
            maxrate = variant.get("max_bitrate")
            if maxrate is None:
                continue
            try:
                maxrate_int = _bitrate_to_int(str(maxrate))
            except Exception:  # noqa: BLE001
                continue

            if bitrate is None:
                variant["bitrate"] = str(maxrate)
                continue

            try:
                bitrate_int = _bitrate_to_int(str(bitrate))
            except Exception:  # noqa: BLE001
                variant["bitrate"] = str(maxrate)
                continue

            if bitrate_int > maxrate_int:
                variant["bitrate"] = str(maxrate)
            profile[variant_key] = variant

    raw["profiles"] = profiles
    return raw


def _validate_bframe_chain(
    profile: str, bframes: int, lookahead: int, adaptive_b_frames: bool
) -> None:
    if bframes < 0 or bframes > 3:
        raise ValueError("B-frames must be between 0 and 3 for Chromecast-friendly streams.")

    if profile.lower() == "baseline" and bframes != 0:
        raise ValueError("Baseline profile forbids B-frames; expected 0.")

    if lookahead < 0 or lookahead > 60:
        raise ValueError("Lookahead depth must be between 0 and 60 frames.")

    if adaptive_b_frames and (lookahead <= 0 or bframes == 0):
        raise ValueError("Adaptive B-frames require lookahead > 0 and at least 1 B-frame.")


def _validate_aq_flags(aq: bool, spatial_aq: bool, temporal_aq: bool) -> None:
    if not aq and (spatial_aq or temporal_aq):
        raise ValueError("When AQ is disabled, spatial_aq and temporal_aq must also be disabled.")


def _validate_encoding_options(
    mode: str,
    preset: str,
    cq: int,
    rc_mode: str,
    max_fps: int,
    audio_channels: int,
    bframes: int,
    lookahead: int,
    adaptive_b_frames: bool,
    aq: bool,
    spatial_aq: bool,
    temporal_aq: bool,
    profile: str,
) -> None:
    if max_fps <= 0 or max_fps > 60:
        raise ValueError("Frame rate must be between 1 and 60 fps.")

    if audio_channels != 2:
        raise ValueError("Audio must remain stereo (2 channels) for Chromecast Gen 2.")

    if mode == "gpu":
        allowed_presets = {"p4", "p5", "p6", "p7"}
        if preset.lower() not in allowed_presets:
            raise ValueError("NVENC preset must be one of p4–p7 for Chromecast-safe outputs.")

        if cq < 0 or cq > 30:
            raise ValueError(
                "NVENC CQ must be between 0 and 30 for stable quality on Gen 2 Chromecasts."
            )

        allowed_rc_modes = {"cq", "vbr", "vbr_hq"}
        if rc_mode.lower() not in allowed_rc_modes:
            raise ValueError("Rate control must be cq, vbr, or vbr_hq for Chromecast-safe outputs.")
    else:
        allowed_presets = {
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        }
        if preset.lower() not in allowed_presets:
            raise ValueError("x264 preset must be a standard CPU preset (e.g. slow, medium).")
        if cq < 0 or cq > 40:
            raise ValueError("CRF must be between 0 and 40 for CPU fallback encodes.")
        allowed_rc_modes = {"crf", "vbr", "cbr"}
        if rc_mode.lower() not in allowed_rc_modes:
            raise ValueError("CPU fallback rate control must be crf, vbr, or cbr.")

    _validate_bframe_chain(profile, bframes, lookahead, adaptive_b_frames)
    _validate_aq_flags(aq, spatial_aq, temporal_aq)


class AudioProfile(BaseModel):
    codec: str
    bitrate: str
    channels: int = Field(default=2, ge=1, le=8)

    @model_validator(mode="after")
    def validate_channels(self) -> "AudioProfile":
        if self.channels != 2:
            raise ValueError("Chromecast Gen 2 supports stereo output; enforce 2 channels.")
        return self


class HardwareProfile(BaseModel):
    mode: str = Field(default="gpu")
    codec: str
    profile: str
    level: str
    resolution: str
    max_fps: int = Field(default=30, gt=0, le=60)
    bitrate: str = Field(default="8M")
    max_bitrate: str
    bufsize: str
    preset: str = Field(default="p6")
    cq: int = Field(default=18, ge=0, le=30)
    rc: str = Field(default="vbr")
    bframes: int = Field(default=2, ge=0, le=3)
    lookahead: int = Field(default=24, ge=0, le=60)
    adaptive_b_frames: bool = Field(default=True)
    aq: bool = Field(default=True)
    spatial_aq: bool = Field(default=True)
    temporal_aq: bool = Field(default=True)
    aq_strength: int = Field(default=7, ge=5, le=10)
    audio: AudioProfile

    @model_validator(mode="after")
    def validate_codecs(cls, values):
        mode = (values.mode or "gpu").lower()
        values.mode = mode
        _validate_codecs(values.codec, values.audio.codec)
        _validate_profile(values.profile, values.level, values.resolution, values.max_fps)
        _validate_resolution(values.resolution)
        _validate_bitrates(values.bitrate, values.max_bitrate, values.bufsize, values.audio.bitrate)

        rc_mode = values.rc.lower()
        if mode == "gpu" and rc_mode == "cbr":
            rc_mode = "vbr"
        values.rc = rc_mode

        _validate_encoding_options(
            mode,
            values.preset,
            values.cq,
            rc_mode,
            values.max_fps,
            values.audio.channels,
            values.bframes,
            values.lookahead,
            values.adaptive_b_frames,
            values.aq,
            values.spatial_aq,
            values.temporal_aq,
            values.profile,
        )

        if not values.aq:
            values.spatial_aq = False
            values.temporal_aq = False

        return values


class ProfileSet(BaseModel):
    gpu: HardwareProfile
    cpu: HardwareProfile

    @model_validator(mode="after")
    def ensure_modes(self):
        self.gpu.mode = "gpu"
        self.cpu.mode = "cpu"
        return self


class LibraryConfig(BaseModel):
    root: str
    depth: str
    profile: str
    profile_id: Optional[int] = None


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
    profiles: Dict[str, ProfileSet]
    operational: OperationConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    notifiers: Dict[str, dict] = Field(default_factory=dict)
    jellyfin: Optional[JellyfinConfig] = None

    def profile_for(self, library_name: str) -> ProfileSet:
        library = self.libraries[library_name]
        profile_name = library.profile
        if profile_name not in self.profiles:
            raise ValueError(f"Profile {profile_name} is not defined.")
        return self.profiles[profile_name]

    def profile_named(self, profile_name: str) -> ProfileSet:
        if profile_name not in self.profiles:
            raise ValueError(f"Profile {profile_name} is not defined.")
        return self.profiles[profile_name]


@dataclass
class ConfigSnapshot:
    config: QualityConfig
    revision: float


class ConfigStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def _seed_if_empty(self) -> None:
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM config")
            if cursor.fetchone()[0] > 0:
                return

        LOGGER.info("Seeding configuration database from built-in defaults")
        raw = copy.deepcopy(DEFAULT_CONFIG)
        seed_source = "builtin"

        snapshot = self.save_config(QualityConfig(**raw), source=seed_source)
        LOGGER.info(
            "Seeded configuration database (revision %s) from %s", snapshot.revision, seed_source
        )

    def load_config(self) -> ConfigSnapshot:
        self._seed_if_empty()
        with self._lock:
            row = self._conn.execute(
                "SELECT value, updated_at FROM config WHERE key = ?", ("quality",)
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Configuration has not been initialized")
        raw = _sanitize_profiles(json.loads(row["value"]))
        try:
            config = QualityConfig(**raw)
        except ValidationError as exc:
            LOGGER.error("Failed to validate stored configuration: %s", exc)
            raise
        return ConfigSnapshot(config=config, revision=row["updated_at"])

    def save_config(self, config: QualityConfig, *, source: Optional[str] = None) -> ConfigSnapshot:
        validated = QualityConfig(**config.model_dump())
        payload = json.dumps(validated.model_dump())
        revision = datetime.now(timezone.utc).timestamp()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO config(key, value, updated_at) VALUES (?, ?, ?)",
                ("quality", payload, revision),
            )
            if source:
                self._conn.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                    ("config_seed_source", source),
                )
            self._conn.commit()
        LOGGER.debug("Persisted configuration revision %s to %s", revision, self.db_path)
        return ConfigSnapshot(config=validated, revision=revision)


class ConfigService:
    def __init__(self, db_path: Path) -> None:
        self.store = ConfigStore(db_path)
        self._snapshot: Optional[ConfigSnapshot] = None

    @property
    def snapshot(self) -> ConfigSnapshot:
        if self._snapshot is None:
            self._snapshot = self.store.load_config()
        return self._snapshot

    def reload(self) -> ConfigSnapshot:
        self._snapshot = self.store.load_config()
        return self._snapshot

    def update_profile(self, name: str, data: dict) -> ConfigSnapshot:
        profile = ProfileSet(**data)
        config = self.snapshot.config
        config.profiles[name] = profile
        LOGGER.info("Updated encoding profile '%s' for Chromecast-safe settings.", name)
        self._snapshot = self.store.save_config(config)
        return self._snapshot

    def update_logging(self, retention_days: int) -> ConfigSnapshot:
        config = self.snapshot.config
        config.logging.retention_days = retention_days
        LOGGER.info("Updated log retention to %s days", retention_days)
        self._snapshot = self.store.save_config(config)
        return self._snapshot

    def upsert_library(
        self, name: str, *, root: str, depth: str, profile: str, profile_id: int
    ) -> ConfigSnapshot:
        config = self.snapshot.config
        config.libraries[name] = LibraryConfig(
            root=root,
            depth=depth,
            profile=profile,
            profile_id=profile_id,
        )
        LOGGER.info(
            "Updated library '%s' (root=%s, depth=%s, profile=%s)", name, root, depth, profile
        )
        self._snapshot = self.store.save_config(config)
        return self._snapshot

    def delete_library(self, name: str) -> ConfigSnapshot:
        config = self.snapshot.config
        if name in config.libraries:
            del config.libraries[name]
            LOGGER.info("Removed library '%s' from configuration store", name)
        self._snapshot = self.store.save_config(config)
        return self._snapshot

    def reset_to_defaults(self) -> ConfigSnapshot:
        raw = copy.deepcopy(DEFAULT_CONFIG)
        self._snapshot = self.store.save_config(QualityConfig(**raw), source="reset")
        LOGGER.warning("Reset configuration to built-in defaults")
        return self._snapshot


def sanitize_config(config: QualityConfig, *, revision: Optional[float] = None) -> Dict[str, Any]:
    data = config.model_dump()
    jellyfin_cfg = data.get("jellyfin")
    if jellyfin_cfg:
        jellyfin_cfg["api_key"] = "REDACTED"
    if revision is not None:
        data["revision"] = revision
    return data
