from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from app.library_entries import LibraryStatus
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError


def _build_test_app(tmp_path: Path, monkeypatch, fake_redis):
    monkeypatch.setenv("CONFIG_DB_PATH", str(tmp_path / "config.db"))
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "logs.db"))
    monkeypatch.setenv("LIBRARY_DB_PATH", str(tmp_path / "library.db"))

    for module in [
        "app",
        "app.main",
        "app.jobs",
        "app.profiles",
        "app.library_entries",
        "app.job_history",
        "app.logs",
        "app.config",
        "app.db",
        "app.dependencies",
        "app.services",
        "app.services.core",
        "app.routers",
        "app.routers.system",
        "app.routers.jobs",
        "app.routers.libraries",
        "app.routers.config",
        "app.routers.logs",
    ]:
        sys.modules.pop(module, None)

    import app.jobs as jobs

    monkeypatch.setattr(jobs.redis, "from_url", lambda url, decode_responses=True: fake_redis)

    import app.main as main

    importlib.reload(main)
    return main.app, main


@pytest.fixture()
def test_app(tmp_path, monkeypatch, fake_redis):
    app, main = _build_test_app(tmp_path, monkeypatch, fake_redis)
    return TestClient(app), main


def _first_profile_id(client: TestClient) -> int:
    profiles = client.get("/api/profiles").json()
    assert profiles, "Profiles should be seeded"
    return profiles[0]["id"]


def _first_profile_name(client: TestClient) -> str:
    profiles = client.get("/api/profiles").json()
    assert profiles
    return profiles[0]["name"]


def test_events_ingest_creates_entry(test_app, tmp_path):
    client, _main = test_app
    profile_id = _first_profile_id(client)

    media_root = tmp_path / "ingest"
    media_root.mkdir()
    payload = {"name": "runtime", "root": str(media_root), "depth": "max", "profile_id": profile_id}
    create_resp = client.post("/api/libraries", json=payload)
    assert create_resp.status_code == 201

    media_file = media_root / "demo.mkv"
    media_file.write_bytes(b"demo")

    events_payload = {
        "events": [
            {
                "path": str(media_file),
                "library": "runtime",
                "event": "created",
                "is_directory": False,
            }
        ]
    }
    resp = client.post("/api/events", json=events_payload)
    assert resp.status_code == 200

    entries_response = client.get(
        "/api/library/entries",
        params={"library": "runtime", "include_total": "true"},
    ).json()
    items = (
        entries_response.get("items") if isinstance(entries_response, dict) else entries_response
    )
    assert items, "Entry should be recorded from watcher events"
    assert items[0]["path"] == str(media_file)
    assert items[0]["status"] in {LibraryStatus.PENDING, LibraryStatus.CONVERTING}


def test_reprocess_endpoint_adds_job(test_app, tmp_path):
    client, main = test_app

    # Ensure cleanup
    client.delete("/api/libraries/runtime")

    profile_id = _first_profile_id(client)
    profile_name = client.get(f"/api/profiles/{profile_id}").json()["name"]

    media_root = tmp_path / "reprocess"
    media_root.mkdir()
    library_payload = {
        "name": "runtime",
        "root": str(media_root),
        "depth": "max",
        "profile_id": profile_id,
    }
    assert client.post("/api/libraries", json=library_payload).status_code == 201

    source = media_root / "movie.mkv"
    source.write_bytes(b"content")
    entry = main.LIBRARY_STORE.update_status(
        str(source),
        LibraryStatus.CONVERTED,
        library="runtime",
        profile=profile_name,
        profile_id=profile_id,
        job_id="seed-job",
        output_path=str(main.job_manager.output_path(source)),
        original_missing=False,
    )

    response = client.post(
        f"/api/library/entries/{entry.id}/reprocess", json={"profile_id": profile_id}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["entry"]["status"] == LibraryStatus.PENDING
    assert payload["job"]["library"] == "runtime"


def test_library_add_and_delete_marks_entries(test_app, tmp_path):
    client, main = test_app
    profile_id = _first_profile_id(client)

    media_root = tmp_path / "librarydel"
    media_root.mkdir()
    create = client.post(
        "/api/libraries",
        json={"name": "temp", "root": str(media_root), "depth": "max", "profile_id": profile_id},
    )
    assert create.status_code == 201

    media_file = media_root / "demo.mkv"
    media_file.write_bytes(b"demo")
    client.post(
        "/api/events",
        json={
            "events": [
                {
                    "path": str(media_file),
                    "library": "temp",
                    "event": "created",
                    "is_directory": False,
                }
            ]
        },
    )

    delete_resp = client.delete("/api/libraries/temp")
    assert delete_resp.status_code == 200
    remaining = client.get("/api/libraries").json()
    assert all(item["name"] != "temp" for item in remaining)

    entries = client.get("/api/library/entries", params={"library": "temp"}).json()
    assert entries
    assert all(entry["status"] == LibraryStatus.REMOVED for entry in entries)


def test_websocket_pushes_library_update(test_app, tmp_path):
    client, _main = test_app
    profile_id = _first_profile_id(client)
    media_root = tmp_path / "ws"
    media_root.mkdir()

    with client.websocket_connect("/ws") as websocket:
        create_resp = client.post(
            "/api/libraries",
            json={
                "name": "ws-lib",
                "root": str(media_root),
                "depth": "max",
                "profile_id": profile_id,
            },
        )
        assert create_resp.status_code == 201
        message = websocket.receive_json()
        assert message["type"] == "library-update"
        assert message["action"] == "created"


def test_clear_jobs_endpoint_removes_completed_jobs(test_app, tmp_path):
    client, main = test_app
    import app.jobs as jobs_module

    profile_id = _first_profile_id(client)
    profile_name = _first_profile_name(client)

    media_root = tmp_path / "clear"
    media_root.mkdir()
    library_payload = {
        "name": "clear-lib",
        "root": str(media_root),
        "profile_id": profile_id,
    }
    assert client.post("/api/libraries", json=library_payload).status_code == 201

    source = media_root / "sample.mkv"
    source.write_bytes(b"data")

    job = asyncio.run(
        main.job_manager.add_job(
            str(source),
            "clear-lib",
            profile_name,
            profile_id=profile_id,
            encoding=main.encoding_payload(profile_id),
        )
    )

    asyncio.run(
        main.job_manager.update_job(
            job.id,
            jobs_module.JobStatusUpdate(status=jobs_module.JobStatus.COMPLETED, progress=100),
        )
    )

    response = client.post("/api/jobs/clear")
    assert response.status_code == 200
    assert response.json()["removed"] >= 1

    jobs_after = client.get("/api/jobs").json()
    assert all(item["status"] != jobs_module.JobStatus.COMPLETED for item in jobs_after)


def test_next_job_reports_queue_outage(test_app, monkeypatch):
    client, _main = test_app
    failure = AsyncMock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr("app.routers.jobs.job_manager.queue_state", failure)

    response = client.get("/api/jobs/next")
    assert response.status_code == 503
    assert response.json()["detail"] == "Job queue unavailable"
