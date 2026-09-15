# Add services/gpu-ffmpeg to path so we can import app
import asyncio
import sys
from pathlib import Path
from typing import cast

import httpx

sys.path.append(str(Path(__file__).parents[1]))

from app import worker  # type: ignore  # noqa: E402

# update_job_status is monkeypatched in every test, so no HTTP client is used.
NO_CLIENT = cast(httpx.AsyncClient, None)


class StatusRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, client, job_id, status, progress, message, **kwargs):
        self.calls.append({"status": status, "message": message, **kwargs})


class FakeBuilder:
    """Records the path ffmpeg would write to and returns a minimal command."""

    def __init__(self, analysis, input_path, output_path, *args, **kwargs):
        self.output_path = output_path

    def build(self):
        return ["ffmpeg", "-i", "input", str(self.output_path)]


def _patch_process_job(monkeypatch, recorder, *, return_code: int):
    monkeypatch.setattr(worker, "update_job_status", recorder)
    monkeypatch.setattr(worker, "FFmpegBuilder", FakeBuilder)

    async def fake_gpu_ready(client, job_id):
        return True

    monkeypatch.setattr(worker, "_ensure_gpu_ready", fake_gpu_ready)

    def fake_probe_file(path):
        return {"format": {"duration": "120.0"}, "streams": []}

    monkeypatch.setattr(worker, "probe_file", fake_probe_file)

    async def fake_duration(source):
        return 120.0

    monkeypatch.setattr(worker, "_probe_duration", fake_duration)

    async def fake_encoding(job, client):
        return {"pipeline": {}}

    monkeypatch.setattr(worker, "resolve_encoding_for_job", fake_encoding)

    async def fake_run_conversion(command, progress_callback, timeout=None, idle_timeout=None):
        # Simulate ffmpeg creating its output (the temporary path) before exit.
        Path(command[-1]).write_bytes(b"encoded content")
        return return_code, []

    monkeypatch.setattr(worker, "run_conversion", fake_run_conversion)

    async def fake_sidecars(*args, **kwargs):
        return []

    monkeypatch.setattr(worker, "_extract_text_subtitles", fake_sidecars)

    async def fake_compliance(output_path):
        return {"compliant": True, "issues": []}

    monkeypatch.setattr(worker, "_probe_output_compliance", fake_compliance)

    async def fake_report(*args, **kwargs):
        return {"source_streams": 0, "embedded": 0, "sidecars": 0, "preserved": True}

    monkeypatch.setattr(worker, "_subtitle_report", fake_report)

    async def fake_remove_original(*args, **kwargs):
        return False

    monkeypatch.setattr(worker, "_maybe_remove_original", fake_remove_original)


def test_temporary_output_path_keeps_mp4_suffix_and_part_marker():
    output = Path("/media/series/episode-chromecast.mp4")
    temp = worker._temporary_output_path(output)
    assert temp != output
    assert temp.suffix == ".mp4"
    assert temp.name == "episode-chromecast.part.mp4"


def test_successful_encode_publishes_validated_output_atomically(monkeypatch, tmp_path):
    recorder = StatusRecorder()
    _patch_process_job(monkeypatch, recorder, return_code=0)

    source = tmp_path / "episode.mkv"
    source.write_bytes(b"source content")

    job = {"id": "job-convert-1", "path": str(source), "request_id": None, "job_type": "convert"}
    asyncio.run(worker.process_job(client=NO_CLIENT, job=job))

    final = tmp_path / "episode-chromecast.mp4"
    temp = tmp_path / "episode-chromecast.part.mp4"
    assert final.exists(), "validated output must be published at the final path"
    assert final.read_bytes() == b"encoded content"
    assert not temp.exists(), "temporary output must not survive a successful encode"
    assert recorder.calls[-1]["status"] == "completed"


def test_failed_encode_leaves_no_output_or_partial_file(monkeypatch, tmp_path):
    recorder = StatusRecorder()
    _patch_process_job(monkeypatch, recorder, return_code=1)

    source = tmp_path / "episode.mkv"
    source.write_bytes(b"source content")

    job = {"id": "job-convert-2", "path": str(source), "request_id": None, "job_type": "convert"}
    asyncio.run(worker.process_job(client=NO_CLIENT, job=job))

    assert not (tmp_path / "episode-chromecast.mp4").exists()
    assert not (tmp_path / "episode-chromecast.part.mp4").exists()
    assert recorder.calls[-1]["status"] == "failed"
