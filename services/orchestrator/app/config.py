from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

LOGGER = logging.getLogger("orchestrator.config")


class AudioProfile(BaseModel):
    codec: str
    bitrate: str


class Profile(BaseModel):
    codec: str
    profile: str
    level: str
    resolution: str
    max_bitrate: str
    bufsize: str
    audio: AudioProfile

    @model_validator(mode="after")
    def validate_codecs(cls, values):
        codec = values.codec
        audio = values.audio
        if codec.lower() != "h264":
            raise ValueError("Only H.264 is supported to keep Chromecast compatibility.")
        if audio.codec.lower() != "aac":
            raise ValueError("Audio codec must be AAC for Chromecast.")
        return values


class LibraryConfig(BaseModel):
    root: str
    depth: str
    profile: str


class OperationConfig(BaseModel):
    max_concurrent_jobs: int
    gpu_temperature_cutoff: int
    max_disk_usage_percent: int


class JellyfinConfig(BaseModel):
    url: str
    api_key: str
    libraries: Dict[str, int]


class QualityConfig(BaseModel):
    libraries: Dict[str, LibraryConfig]
    profiles: Dict[str, Profile]
    operational: OperationConfig
    notifiers: Dict[str, dict] = Field(default_factory=dict)
    jellyfin: Optional[JellyfinConfig] = None

    def profile_for(self, library_name: str) -> Profile:
        library = self.libraries[library_name]
        profile_name = library.profile
        if profile_name not in self.profiles:
            raise ValueError(f"Profile {profile_name} is not defined.")
        return self.profiles[profile_name]


@dataclass
class ConfigSource:
    path: Path
    config: QualityConfig


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
