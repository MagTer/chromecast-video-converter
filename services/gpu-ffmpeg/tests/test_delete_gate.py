# Add services/gpu-ffmpeg to path so we can import app
import asyncio
import sys
from pathlib import Path
from typing import cast

import httpx

sys.path.append(str(Path(__file__).parents[1]))

from app import worker  # type: ignore  # noqa: E402

# The recorder replaces update_job_status, so no HTTP client is ever used.
NO_CLIENT = cast(httpx.AsyncClient, None)


class StatusRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, client, job_id, status, progress, message, **kwargs):
        self.calls.append({"status": status, "message": message, **kwargs})


def _setup_media(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"original content")
    output = tmp_path / "movie-chromecast.mp4"
    output.write_bytes(b"converted content")
    return source, output


def _run_delete_job(monkeypatch, source: Path, *, valid_output: bool, compliance: dict):
    recorder = StatusRecorder()
    monkeypatch.setattr(worker, "update_job_status", recorder)

    async def fake_validate(output, expected_duration):
        return valid_output

    async def fake_compliance(output_path):
        return compliance

    async def fake_duration(path):
        return 120.0

    monkeypatch.setattr(worker, "_validate_output", fake_validate)
    monkeypatch.setattr(worker, "_probe_output_compliance", fake_compliance)
    monkeypatch.setattr(worker, "_probe_duration", fake_duration)

    job = {"id": "job-delete-1", "path": str(source), "request_id": None}
    asyncio.run(worker.handle_delete_job(client=NO_CLIENT, job=job))
    return recorder


def test_delete_job_refuses_invalid_output(monkeypatch, tmp_path):
    source, _output = _setup_media(tmp_path)
    recorder = _run_delete_job(
        monkeypatch, source, valid_output=False, compliance={"compliant": True}
    )
    assert source.exists(), "Original must survive a failed output validation"
    assert recorder.calls[-1]["status"] == "failed"
    assert "Refusing to delete" in recorder.calls[-1]["message"]


def test_delete_job_refuses_noncompliant_output(monkeypatch, tmp_path):
    source, _output = _setup_media(tmp_path)
    recorder = _run_delete_job(
        monkeypatch,
        source,
        valid_output=True,
        compliance={"compliant": False, "issues": ["Width 2592 exceeds 1920"]},
    )
    assert source.exists(), "Original must survive a non-compliant verdict"
    assert recorder.calls[-1]["status"] == "failed"
    assert "not Chromecast compliant" in recorder.calls[-1]["message"]
    assert "Width 2592 exceeds 1920" in recorder.calls[-1]["message"]


def test_delete_job_deletes_after_gate_passes(monkeypatch, tmp_path):
    source, _output = _setup_media(tmp_path)
    recorder = _run_delete_job(
        monkeypatch, source, valid_output=True, compliance={"compliant": True}
    )
    assert not source.exists(), "Original should be deleted once the gate passes"
    assert recorder.calls[-1]["status"] == "completed"


def test_maybe_remove_original_respects_compliance(monkeypatch, tmp_path):
    source, output = _setup_media(tmp_path)
    monkeypatch.setattr(worker, "REMOVE_ORIGINAL", True)

    async def fake_validate(out, expected_duration):
        return True

    monkeypatch.setattr(worker, "_validate_output", fake_validate)

    removed = asyncio.run(
        worker._maybe_remove_original(
            source, output, 120.0, {"compliant": False, "issues": ["bad level"]}
        )
    )
    assert removed is False
    assert source.exists()

    removed = asyncio.run(worker._maybe_remove_original(source, output, 120.0, {"compliant": True}))
    assert removed is True
    assert not source.exists()
