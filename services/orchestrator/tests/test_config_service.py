from __future__ import annotations

from pathlib import Path

import pytest
from app.config import ConfigService, JellyfinConfig, sanitize_config


@pytest.fixture()
def config_db(tmp_path: Path):
    return tmp_path / "config.db"


def test_config_service_seed_and_updates(config_db):
    service = ConfigService(config_db)

    snapshot = service.snapshot
    assert snapshot.config.profiles
    assert snapshot.config.libraries

    updated = service.update_logging(14)
    assert updated.config.logging.retention_days == 14

    chromecast_profile = updated.config.profile_named("chromecast").model_dump()
    chromecast_profile["gpu"]["cq"] = 20
    refreshed = service.update_profile("chromecast", chromecast_profile)
    assert refreshed.config.profile_named("chromecast").gpu.cq == 20

    jellyfin_config = JellyfinConfig(
        url="http://localhost", api_key="SECRET", libraries={"movies": 1}
    )
    refreshed.config.jellyfin = jellyfin_config
    sanitized = sanitize_config(refreshed.config, revision=123.0)
    assert sanitized["jellyfin"]["api_key"] == "REDACTED"
    assert sanitized["revision"] == 123.0


def test_reload_refreshes_snapshot(config_db):
    service = ConfigService(config_db)

    first_revision = service.snapshot.revision
    service.update_logging(10)
    second_revision = service.reload().revision

    assert second_revision >= first_revision
