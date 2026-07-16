from __future__ import annotations

import importlib
import os
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
        "app.utils",
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
        "app.routers.history",
        "app.routers.logs",
        "app.schemas",
    ]:
        sys.modules.pop(module, None)

    import app.jobs as jobs

    monkeypatch.setattr(jobs.redis, "from_url", lambda url, decode_responses=True: fake_redis())

    import app.main as main

    importlib.reload(main)

    # Ensure config is seeded
    from app.dependencies import get_app_dependencies
    from app.main import seed_profiles_and_libraries

    config_service = get_app_dependencies().config_service
    snapshot = config_service.reload()
    seed_profiles_and_libraries(snapshot)

    return main.app, main


@pytest.fixture()
def test_app(tmp_path, monkeypatch, fake_redis):
    app, main = _build_test_app(tmp_path, monkeypatch, fake_redis)
    with TestClient(app) as client:
        yield client, main


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

    # include_total wraps the response in an envelope with pagination metadata.
    envelope = client.get(
        "/api/library/entries",
        params={"library": "runtime", "include_total": "true", "limit": 1, "offset": 0},
    ).json()
    assert isinstance(envelope, dict)
    assert envelope["total"] >= 1
    assert envelope["limit"] == 1
    assert envelope["offset"] == 0
    assert len(envelope["items"]) == 1

    # Without include_total the legacy bare-list shape is preserved.
    bare = client.get("/api/library/entries", params={"library": "runtime"}).json()
    assert isinstance(bare, list)


def test_event_paths_normalized_to_canonical(tmp_path, monkeypatch, fake_redis):
    media_root = tmp_path / "canon-root"
    watch_root = tmp_path / "watch-root"
    media_root.mkdir(parents=True)
    try:
        os.symlink(media_root, watch_root)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported on this platform")

    monkeypatch.setenv("DISPLAY_LIBRARY_PREFIX", str(media_root))
    monkeypatch.setenv("LIBRARY_ROOT_PREFIXES", f"{watch_root},{media_root}")

    app, _main = _build_test_app(tmp_path, monkeypatch, fake_redis)

    with TestClient(app) as client:
        profile_id = _first_profile_id(client)

        create_payload = {
            "name": "mirror",
            "root": str(media_root),
            "depth": "max",
            "profile_id": profile_id,
        }
        assert client.post("/api/libraries", json=create_payload).status_code == 201

        media_file = media_root / "dup.mkv"
        media_file.write_bytes(b"demo")
        watch_path = watch_root / "dup.mkv"

        event_body = {
            "events": [
                {
                    "path": str(watch_path),
                    "library": "mirror",
                    "event": "created",
                    "is_directory": False,
                }
            ]
        }
        assert client.post("/api/events", json=event_body).status_code == 200

        entries = client.get("/api/library/entries", params={"library": "mirror"}).json()
        assert len(entries) == 1
        assert entries[0]["path"] == str(media_file)

        jobs_payload = client.get("/api/jobs").json()
        assert len(jobs_payload) == 1
        assert jobs_payload[0]["path"] == str(media_file)

        # Re-send event using canonical path and ensure duplicates are not created.
        second_event = {
            "path": str(media_file),
            "library": "mirror",
            "event": "created",
            "is_directory": False,
        }
        assert client.post("/api/events", json=second_event).status_code == 200

        # No need to reset redis manually if using context manager properly
        # get_app_dependencies().job_manager._redis = None

        entries_after = client.get("/api/library/entries", params={"library": "mirror"}).json()
        assert len(entries_after) == 1
        assert entries_after[0]["path"] == str(media_file)

        jobs_after = client.get("/api/jobs").json()
        assert len(jobs_after) == 1
        assert jobs_after[0]["path"] == str(media_file)


def test_purge_inactive_jobs_endpoint(test_app, tmp_path):
    client, _main = test_app
    profile_id = _first_profile_id(client)

    media_root = tmp_path / "purge"
    media_root.mkdir()

    create = client.post(
        "/api/libraries",
        json={"name": "purge", "root": str(media_root), "depth": "max", "profile_id": profile_id},
    )
    assert create.status_code == 201

    media_file = media_root / "stale.mkv"
    media_file.write_bytes(b"demo")
    client.post(
        "/api/events",
        json={
            "events": [
                {
                    "path": str(media_file),
                    "library": "purge",
                    "event": "created",
                    "is_directory": False,
                }
            ]
        },
    )

    jobs_before = client.get("/api/jobs").json()
    assert jobs_before, "job should be queued"

    response = client.post("/api/jobs/purge-inactive")
    payload = response.json()
    assert response.status_code == 200
    assert payload["removed_jobs"] >= 1

    jobs_after = client.get("/api/jobs").json()
    assert jobs_after == []


def test_reprocess_endpoint_adds_job(test_app, tmp_path):
    client, main = test_app
    from app.dependencies import get_app_dependencies

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
    entry = get_app_dependencies().library_entry_store.update_status(
        str(source),
        LibraryStatus.CONVERTED,
        library="runtime",
        profile=profile_name,
        profile_id=profile_id,
        job_id="seed-job",
        output_path=str(get_app_dependencies().job_manager.output_path(source)),
        original_missing=False,
    )

    response = client.post(
        f"/api/library/entries/{entry.id}/reprocess", json={"profile_id": profile_id}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["entry"]["status"] == LibraryStatus.PENDING
    assert payload["job"]["library"] == "runtime"


def test_verify_endpoint_persists_compliance_without_status_change(test_app, tmp_path):
    client, main = test_app
    from app.dependencies import get_app_dependencies

    client.delete("/api/libraries/runtime")
    profile_id = _first_profile_id(client)
    profile_name = client.get(f"/api/profiles/{profile_id}").json()["name"]

    media_root = tmp_path / "verify"
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
    entry = get_app_dependencies().library_entry_store.update_status(
        str(source),
        LibraryStatus.CONVERTED,
        library="runtime",
        profile=profile_name,
        profile_id=profile_id,
        job_id="seed-job",
        output_path=str(get_app_dependencies().job_manager.output_path(source)),
        original_missing=False,
    )

    response = client.post(f"/api/library/entries/{entry.id}/verify")
    assert response.status_code == 200
    job_payload = response.json()["job"]
    assert job_payload["job_type"] == "verify"

    compliance = {
        "compliant": False,
        "issues": ["Width 2592 exceeds 1920"],
        "checked_at": "2026-07-15T00:00:00+00:00",
        "video": {"width": 2592, "height": 1080},
    }
    status_response = client.post(
        f"/api/jobs/{job_payload['id']}/status",
        json={"status": "completed", "progress": 100, "compliance": compliance},
    )
    assert status_response.status_code == 200

    entries = client.get("/api/library/entries", params={"library": "runtime"}).json()
    stored = next(item for item in entries if item["id"] == entry.id)
    assert stored["status"] == LibraryStatus.CONVERTED
    assert stored["output_compliant"] is False
    assert stored["compliance"]["issues"] == ["Width 2592 exceeds 1920"]

    # Entries without a converted output cannot be verified.
    pending_source = media_root / "pending.mkv"
    pending_source.write_bytes(b"content")
    pending_entry = get_app_dependencies().library_entry_store.update_status(
        str(pending_source),
        LibraryStatus.PENDING,
        library="runtime",
        profile=profile_name,
        profile_id=profile_id,
    )
    denied = client.post(f"/api/library/entries/{pending_entry.id}/verify")
    assert denied.status_code == 409


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
    # app.jobs already imported by conftest magic?
    from app.jobs import JobStatus

    profile_id = _first_profile_id(client)

    media_root = tmp_path / "clear"
    media_root.mkdir()
    library_payload = {
        "name": "clear-lib",
        "root": str(media_root),
        "profile_id": profile_id,
    }
    assert client.post("/api/libraries", json=library_payload).status_code == 201

    source = media_root / "movie.mkv"
    source.write_bytes(b"data")

    # Create job via event
    event_body = {
        "events": [
            {
                "path": str(source),
                "library": "clear-lib",
                "event": "created",
                "is_directory": False,
            }
        ]
    }
    assert client.post("/api/events", json=event_body).status_code == 200

    # Retrieve job ID
    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 1
    job_id = jobs[0]["id"]

    # Mark as completed via API
    status_update = {"status": JobStatus.COMPLETED, "progress": 100}
    assert client.post(f"/api/jobs/{job_id}/status", json=status_update).status_code == 200

    # Clear completed
    response = client.post("/api/jobs/clear")
    assert response.status_code == 200
    assert response.json()["removed"] >= 1

    jobs_after = client.get("/api/jobs").json()
    assert all(item["status"] != JobStatus.COMPLETED for item in jobs_after)


def test_next_job_reports_queue_outage(test_app, monkeypatch):
    client, _main = test_app
    failure = AsyncMock(side_effect=ConnectionError("offline"))
    monkeypatch.setattr("app.jobs.JobManager.queue_state", failure)

    response = client.get("/api/jobs/next")
    assert response.status_code == 503
    assert response.json()["detail"] == "Job queue unavailable"


def test_entries_summary_and_compliance_filters(test_app, tmp_path):
    client, _main = test_app
    from app.dependencies import get_app_dependencies

    client.delete("/api/libraries/runtime")
    profile_id = _first_profile_id(client)
    profile_name = client.get(f"/api/profiles/{profile_id}").json()["name"]

    media_root = tmp_path / "summary"
    media_root.mkdir()
    library_payload = {
        "name": "runtime",
        "root": str(media_root),
        "depth": "max",
        "profile_id": profile_id,
    }
    assert client.post("/api/libraries", json=library_payload).status_code == 201

    store = get_app_dependencies().library_entry_store

    def seed(name: str, status: str, compliant=None):
        source = media_root / name
        source.write_bytes(b"content")
        store.update_status(
            str(source),
            status,
            library="runtime",
            profile=profile_name,
            profile_id=profile_id,
        )
        if compliant is not None:
            store.update_compliance(str(source), compliant=compliant, detail="{}")

    seed("ok.mkv", LibraryStatus.CONVERTED, compliant=True)
    seed("bad.mkv", LibraryStatus.CONVERTED, compliant=False)
    seed("fresh.mkv", LibraryStatus.PENDING)
    seed("broken.mkv", LibraryStatus.FAILED)

    # Whole-table totals, independent of any pagination window.
    summary = client.get("/api/library/entries/summary", params={"library": "runtime"}).json()
    assert summary["converted"] == 2
    assert summary["pending"] == 1
    assert summary["failed"] == 1
    assert summary["noncompliant"] == 1
    assert summary["total"] == 4

    def entry_names(params):
        entries = client.get("/api/library/entries", params=params).json()
        return sorted(item["path"].rsplit("/", 1)[-1] for item in entries)

    assert entry_names({"library": "runtime", "compliance": "noncompliant"}) == ["bad.mkv"]
    assert entry_names({"library": "runtime", "compliance": "compliant"}) == ["ok.mkv"]
    assert entry_names({"library": "runtime", "compliance": "unverified"}) == [
        "broken.mkv",
        "fresh.mkv",
    ]
    assert entry_names({"library": "runtime", "query": "bad"}) == ["bad.mkv"]

    # The total in the envelope respects the same filters.
    envelope = client.get(
        "/api/library/entries",
        params={
            "library": "runtime",
            "compliance": "unverified",
            "include_total": "true",
            "limit": 1,
        },
    ).json()
    assert envelope["total"] == 2
    assert len(envelope["items"]) == 1

    assert client.get("/api/library/entries", params={"compliance": "bogus"}).status_code == 400


def test_history_reports_job_type_and_duration(test_app):
    client, _main = test_app
    from datetime import datetime, timedelta, timezone

    from app.dependencies import get_app_dependencies
    from app.job_history import JobHistoryEntry, JobHistoryStatus

    started = datetime.now(timezone.utc) - timedelta(seconds=90)
    completed = started + timedelta(seconds=75)
    get_app_dependencies().job_history_store.record(
        JobHistoryEntry(
            job_id="hist-verify-1",
            path="/watch/movies/demo.mkv",
            library="runtime",
            profile="default",
            status=JobHistoryStatus.COMPLETED,
            job_type="verify",
            started_at=started,
            completed_at=completed,
        )
    )

    history = client.get("/api/history").json()
    row = next(item for item in history if item["id"] == "hist-verify-1")
    assert row["job_type"] == "verify"
    assert row["library"] == "runtime"
    assert abs(row["elapsed_seconds"] - 75) <= 1


def test_upsert_requeue_clears_stale_verdict(test_app, tmp_path):
    _client, _main = test_app
    from app.dependencies import get_app_dependencies
    from app.library_entries import EntryUpdate

    store = get_app_dependencies().library_entry_store
    source = tmp_path / "stale.mkv"
    source.write_bytes(b"content")

    store.update_status(
        str(source),
        LibraryStatus.CONVERTED,
        library="runtime",
        profile="default",
        output_compliant=False,
        compliance_detail='{"compliant": false}',
    )

    # A scan that finds the output missing re-queues the entry via upsert;
    # the old output's verdict must not survive as a stale badge.
    entry = store.upsert(
        EntryUpdate(
            path=str(source),
            library="runtime",
            profile="default",
            status=LibraryStatus.PENDING,
        )
    )
    assert entry.output_compliant is None
    assert entry.compliance_detail is None


def _seed_converted_entry_with_output(client, tmp_path, name: str):
    from app.dependencies import get_app_dependencies

    profile_id = _first_profile_id(client)
    profile_name = client.get(f"/api/profiles/{profile_id}").json()["name"]
    media_root = tmp_path / "deletegate"
    media_root.mkdir(exist_ok=True)
    if not get_app_dependencies().library_config_store.get("deletegate"):
        payload = {
            "name": "deletegate",
            "root": str(media_root),
            "depth": "max",
            "profile_id": profile_id,
        }
        assert client.post("/api/libraries", json=payload).status_code == 201

    source = media_root / name
    source.write_bytes(b"original")
    output = get_app_dependencies().job_manager.output_path(source)
    output.write_bytes(b"converted")
    entry = get_app_dependencies().library_entry_store.update_status(
        str(source),
        LibraryStatus.CONVERTED,
        library="deletegate",
        profile=profile_name,
        profile_id=profile_id,
        output_path=str(output),
    )
    return entry, source, output


def test_remove_original_blocked_for_noncompliant_output(test_app, tmp_path):
    client, _main = test_app
    from app.dependencies import get_app_dependencies

    entry, source, _output = _seed_converted_entry_with_output(client, tmp_path, "bad.mkv")
    get_app_dependencies().library_entry_store.update_compliance(
        str(source), compliant=False, detail='{"compliant": false}'
    )

    response = client.post(f"/api/library/entries/{entry.id}/remove-original")
    assert response.status_code == 409
    assert "not Chromecast compliant" in response.json()["detail"]
    assert source.exists()


def test_remove_original_preserves_verdict_and_failed_delete_restores_entry(test_app, tmp_path):
    client, _main = test_app
    from app.dependencies import get_app_dependencies

    entry, source, _output = _seed_converted_entry_with_output(client, tmp_path, "good.mkv")
    get_app_dependencies().library_entry_store.update_compliance(
        str(source), compliant=True, detail='{"compliant": true}'
    )

    response = client.post(f"/api/library/entries/{entry.id}/remove-original")
    assert response.status_code == 200
    queued = response.json()["entry"]
    assert queued["status"] == LibraryStatus.PENDING
    # Queueing a delete must not wipe the stored verdict.
    assert queued["output_compliant"] is True

    # Claim the delete job as a worker and report the gate refusing it.
    claimed = client.get("/api/jobs/next", params={"worker_id": "gate-test"}).json()
    assert claimed["job_type"] == "delete"
    failed = client.post(
        f"/api/jobs/{claimed['id']}/status",
        json={
            "status": "failed",
            "message": "Refusing to delete original: output failed validation",
        },
    )
    assert failed.status_code == 200
    job_payload = failed.json()
    # Delete jobs never enter the encode retry ladder.
    assert job_payload["status"] == "failed"
    assert job_payload["attempt"] == claimed["attempt"]

    stored = get_app_dependencies().library_entry_store.get(entry.id)
    assert stored.status == LibraryStatus.CONVERTED
    assert source.exists()

    history = client.get("/api/history").json()
    row = next(item for item in history if item["id"] == claimed["id"])
    assert row["job_type"] == "delete"
    assert "Refusing to delete" in row["message"]
