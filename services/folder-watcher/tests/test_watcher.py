import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.watcher import EventManager, WatcherEventHandler


@pytest.fixture
def mock_client():
    client = AsyncMock(spec=httpx.AsyncClient)
    # Ensure post returns a MagicMock, not an AsyncMock, so raise_for_status is synchronous
    client.post.return_value = MagicMock()
    return client


@pytest.fixture
def event_manager(mock_client):
    return EventManager(mock_client)


@pytest.mark.asyncio
async def test_event_manager_send_payload_success(event_manager, mock_client):
    mock_client.post.return_value.status_code = 200
    events = [{"path": "/test", "event": "created"}]

    success = await event_manager._send_payload(events)

    assert success is True
    mock_client.post.assert_called_once_with("/api/events", json={"events": events})


@pytest.mark.asyncio
async def test_event_manager_send_payload_failure(event_manager, mock_client):
    mock_client.post.side_effect = httpx.RequestError("Network Error", request=MagicMock())

    # Speed up retries
    with patch("app.watcher.EVENT_RETRY_BACKOFF_SECONDS", 0):
        with patch("app.watcher.EVENT_RETRY_ATTEMPTS", 2):
            success = await event_manager._send_payload([{}])

    assert success is False
    assert mock_client.post.call_count == 2


def test_watcher_event_handler_created():
    queue = asyncio.Queue()
    handler = WatcherEventHandler("movies", queue)

    event = MagicMock()
    event.is_directory = False
    event.src_path = "/watch/movies/test.mkv"
    event.event_type = "created"

    # Mock exists/stat to avoid FS access
    with patch("pathlib.Path.exists", return_value=True), patch("pathlib.Path.stat") as mock_stat:
        mock_stat.return_value.st_size = 100
        mock_stat.return_value.st_mtime = 1000.0

        handler.on_created(event)

    assert queue.qsize() == 1
    payload = queue.get_nowait()
    assert payload["event"] == "created"
    assert payload["library"] == "movies"
    assert payload["path"] == "/watch/movies/test.mkv"
    assert payload["size"] == 100


def test_watcher_event_handler_moved():
    queue = asyncio.Queue()
    handler = WatcherEventHandler("movies", queue)

    event = MagicMock()
    event.is_directory = False
    event.src_path = "/watch/movies/old.mkv"
    event.dest_path = "/watch/movies/new.mkv"
    event.event_type = "moved"

    with patch("os.path.exists", return_value=True), patch("os.stat") as mock_stat:
        mock_stat.return_value.st_size = 200
        mock_stat.return_value.st_mtime = 2000.0

        handler.on_moved(event)

    assert queue.qsize() == 2

    event1 = queue.get_nowait()
    assert event1["event"] == "deleted"
    assert event1["path"] == "/watch/movies/old.mkv"

    event2 = queue.get_nowait()
    assert event2["event"] == "created"
    assert event2["path"] == "/watch/movies/new.mkv"
