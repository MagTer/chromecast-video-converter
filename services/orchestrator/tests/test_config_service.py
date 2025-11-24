from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.config import DEFAULT_CONFIG, ConfigService, JellyfinConfig, sanitize_config


@pytest.fixture()
def config_paths(tmp_path: Path):
    db_path = tmp_path / "config.db"
    template_path = tmp_path / "settings.yaml.template"
    template_path.write_text(yaml.safe_dump(DEFAULT_CONFIG))
    return db_path, template_path


def test_config_service_seed_and_updates(config_paths):
    db_path, template_path = config_paths
    service = ConfigService(db_path, template_path)

    snapshot = service.snapshot
    assert snapshot.config.profiles
    assert snapshot.config.libraries

    updated = service.update_logging(14)
    assert updated.config.logging.retention_days == 14

    movies_profile = updated.config.profile_named("movies").model_dump()
    movies_profile["cq"] = 20
    refreshed = service.update_profile("movies", movies_profile)
    assert refreshed.config.profile_named("movies").cq == 20

    jellyfin_config = JellyfinConfig(
        url="http://localhost", api_key="SECRET", libraries={"movies": 1}
    )
    refreshed.config.jellyfin = jellyfin_config
    sanitized = sanitize_config(refreshed.config, revision=123.0)
    assert sanitized["jellyfin"]["api_key"] == "REDACTED"
    assert sanitized["revision"] == 123.0


def test_reload_refreshes_snapshot(config_paths):
    db_path, template_path = config_paths
    service = ConfigService(db_path, template_path)

    first_revision = service.snapshot.revision
    service.update_logging(10)
    second_revision = service.reload().revision

    assert second_revision >= first_revision
