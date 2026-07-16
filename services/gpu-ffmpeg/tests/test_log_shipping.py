# Add services/gpu-ffmpeg to path so we can import app
import logging
import sys
from pathlib import Path

import httpx

sys.path.append(str(Path(__file__).parents[1]))

from app import worker  # type: ignore  # noqa: E402


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.posts = []

    def post(self, url, json=None):
        if self.fail:
            raise httpx.TransportError("orchestrator down")
        self.posts.append((url, json))
        return httpx.Response(200, request=httpx.Request("POST", "http://test" + url))


def _make_handler():
    # flush_interval is irrelevant: tests call flush_pending() directly.
    handler = worker.OrchestratorLogHandler("http://test", flush_interval=3600)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _record(message, name="gpu-ffmpeg.builder"):
    return logging.LogRecord(name, logging.INFO, __file__, 1, message, None, None)


def test_handler_batches_and_ships_raw_message():
    handler = _make_handler()
    client = FakeClient()
    handler._client = client

    handler.emit(_record("mapped 2 audio streams"))
    handler.emit(_record("filter chain: scale_cuda=..."))
    handler.flush_pending()

    assert len(client.posts) == 1, "Two records should ship as one batch"
    url, payload = client.posts[0]
    assert url == "/api/logs/ingest"
    entries = payload["entries"]
    assert [e["message"] for e in entries] == [
        "mapped 2 audio streams",
        "filter chain: scale_cuda=...",
    ]
    # Raw message only — no timestamp/level prefix baked into the text.
    assert not entries[0]["message"].startswith("20")
    assert entries[0]["logger"] == "gpu-ffmpeg.builder"
    assert entries[0]["source"] == "gpu-ffmpeg"
    assert entries[0]["category"] == "builder"
    assert "request_id" in entries[0]


def test_handler_requeues_batch_on_transport_failure():
    handler = _make_handler()
    failing = FakeClient(fail=True)
    handler._client = failing

    handler.emit(_record("line during outage"))
    handler.flush_pending()
    assert failing.posts == []

    # Orchestrator comes back: the buffered line ships on the next flush.
    working = FakeClient()
    handler._client = working
    handler.flush_pending()
    assert len(working.posts) == 1
    assert working.posts[0][1]["entries"][0]["message"] == "line during outage"


def test_handler_buffer_is_bounded():
    handler = worker.OrchestratorLogHandler("http://test", flush_interval=3600, max_buffer=5)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._client = FakeClient(fail=True)

    for i in range(20):
        handler.emit(_record(f"line {i}"))
    assert len(handler._buffer) == 5
    # Newest lines are the ones kept.
    assert handler._buffer[-1]["message"] == "line 19"


def test_builder_logs_propagate_to_service_handler():
    # configure_logging attaches the handler to the "gpu-ffmpeg" parent, so
    # sibling module loggers (builder, capabilities) must reach it too.
    service_logger = logging.getLogger("gpu-ffmpeg")
    handlers = [h for h in service_logger.handlers if isinstance(h, worker.OrchestratorLogHandler)]
    assert handlers, "OrchestratorLogHandler must sit on the gpu-ffmpeg root logger"
    handler = handlers[0]
    handler._client = FakeClient()

    # Pytest raises the root level to WARNING; pin the service logger so the
    # propagation path (not the level chain) is what this test exercises.
    previous_level = service_logger.level
    service_logger.setLevel(logging.INFO)
    try:
        with handler._buffer_lock:
            handler._buffer.clear()
        logging.getLogger("gpu-ffmpeg.builder").info("stream mapping decision")
        logging.getLogger("gpu-ffmpeg.capabilities").warning("filter missing")
        messages = [e["message"] for e in handler._buffer]
        assert "stream mapping decision" in messages
        assert "filter missing" in messages
    finally:
        service_logger.setLevel(previous_level)
