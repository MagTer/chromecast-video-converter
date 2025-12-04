from __future__ import annotations

import sys
from pathlib import Path

import fakeredis.aioredis
import pytest
from app.dependencies import AppDependencies


@pytest.fixture
def fake_redis():
    server = fakeredis.FakeServer()

    def _factory():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    return _factory


@pytest.fixture(autouse=True)
def isolated_app_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Ensure app.dependencies is reloaded for each test
    if "app.dependencies" in sys.modules:
        del sys.modules["app.dependencies"]

    # Set DATA_DIR to a temporary path for the duration of the tests
    tmp_data = tmp_path / "data"
    tmp_data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(tmp_data))

    # Import app.dependencies AFTER DATA_DIR is set
    import app.dependencies as dependencies

    # Create a test-specific AppDependencies instance
    test_app_dependencies = AppDependencies(tmp_data)

    # Monkeypatch the module's _global_app_dependencies to our test instance
    monkeypatch.setattr(dependencies, "_global_app_dependencies", test_app_dependencies)

    yield test_app_dependencies
