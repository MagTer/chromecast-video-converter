from __future__ import annotations

import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

try:
    from app.dependencies import AppDependencies
except ImportError:
    AppDependencies = None


@pytest.fixture
def fake_redis():
    def _factory():
        # Create a new FakeRedis instance each time to ensure it binds to the current
        # event loop (especially important when running via TestClient/AnyIO).
        # We drop the shared FakeServer for now as tests currently interact via API.
        return fakeredis.aioredis.FakeRedis(decode_responses=True)

    return _factory


@pytest.fixture(autouse=True)
def isolated_app_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if AppDependencies is None:
        yield
        return

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
