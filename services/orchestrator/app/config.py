from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

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
class ConfigSnapshot:
    config: QualityConfig
    revision: float


class ConfigStore:
    def __init__(
        self, db_path: Path, template_path: Path, legacy_path: Optional[Path] = None
    ) -> None:
        self.db_path = db_path
        self.template_path = template_path
        self.legacy_path = legacy_path
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

        source_path = self.legacy_path if self.legacy_path and self.legacy_path.exists() else None
        if source_path:
            LOGGER.info("Seeding configuration database from legacy file %s", source_path)
        else:
            source_path = self.template_path
            LOGGER.info("Seeding configuration database from template %s", source_path)

        if not source_path.exists():
            raise FileNotFoundError(f"Configuration seed not found at {source_path}")

        raw = yaml.safe_load(source_path.read_text()) or {}
        snapshot = self.save_config(QualityConfig(**raw), source=str(source_path))
        LOGGER.info(
            "Seeded configuration database (revision %s) from %s", snapshot.revision, source_path
        )

    def load_config(self) -> ConfigSnapshot:
        self._seed_if_empty()
        with self._lock:
            row = self._conn.execute(
                "SELECT value, updated_at FROM config WHERE key = ?", ("quality",)
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Configuration has not been initialized")
        raw = json.loads(row["value"])
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
    def __init__(
        self, db_path: Path, template_path: Path, legacy_path: Optional[Path] = None
    ) -> None:
        self.store = ConfigStore(db_path, template_path, legacy_path)
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
        profile = Profile(**data)
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


def sanitize_config(config: QualityConfig, *, revision: Optional[float] = None) -> Dict[str, Any]:
    data = config.model_dump()
    jellyfin_cfg = data.get("jellyfin")
    if jellyfin_cfg:
        jellyfin_cfg["api_key"] = "REDACTED"
    if revision is not None:
        data["revision"] = revision
    return data
