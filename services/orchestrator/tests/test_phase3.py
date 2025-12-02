from datetime import datetime

from app.config import DEFAULT_CONFIG
from app.dependencies import get_app_dependencies
from app.job_history import JobHistoryEntry
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_reset_config():
    # Modify config first
    config_service = get_app_dependencies().config_service
    config_service.update_logging(retention_days=30)
    assert config_service.snapshot.config.logging.retention_days == 30

    response = client.post("/api/config/reset")
    assert response.status_code == 200

    # Check if reset to default (7 days)
    assert (
        config_service.snapshot.config.logging.retention_days
        == DEFAULT_CONFIG["logging"]["retention_days"]
    )


def test_history_endpoint():
    # Seed history
    get_app_dependencies().job_history_store.record(
        JobHistoryEntry(
            job_id="test-job-1",
            path="/test/movie.mkv",
            library="chromecast",
            profile="chromecast",
            status="completed",
            message="Done",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
    )

    response = client.get("/api/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    entry = next(item for item in data if item["id"] == "test-job-1")
    assert entry["status"] == "completed"
    assert "elapsed_seconds" in entry