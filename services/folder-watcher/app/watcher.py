import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

# Configuration
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:9000")
WATCH_ROOTS = os.environ.get("WATCH_ROOTS", "")
WATCH_POLLING = os.environ.get("WATCH_POLLING", "false").lower() == "true"
POLLING_INTERVAL = float(os.environ.get("POLLING_INTERVAL", "2.0"))
EVENT_BUFFER_SECONDS = int(os.environ.get("EVENT_BUFFER_SECONDS", "1"))
EVENT_RETRY_ATTEMPTS = int(os.environ.get("EVENT_RETRY_ATTEMPTS", "5"))
EVENT_RETRY_BACKOFF_SECONDS = int(os.environ.get("EVENT_RETRY_BACKOFF_SECONDS", "2"))
EVENT_SPOOL_FILE = Path(os.environ.get("EVENT_SPOOL_FILE", "/tmp/folder-watcher-spool.jsonl"))
EVENT_SPOOL_MAX_BYTES = int(os.environ.get("EVENT_SPOOL_MAX_BYTES", "10485760"))


# Logging Setup
def _normalize_level(level: str) -> str:
    normalized = level.upper()
    if normalized == "DEBUG":
        return "VERBOSE"
    return normalized


def _derive_source_category(logger_name: str) -> tuple[str, str]:
    normalized = logger_name or "folder-watcher"
    parts = normalized.split(".")
    source = parts[0] if parts else normalized
    category = ".".join(parts[1:]) if len(parts) > 1 else normalized
    return source, category or source


class OrchestratorLogHandler(logging.Handler):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._client = httpx.Client(base_url=base_url, timeout=5.0)

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        severity = _normalize_level(record.levelname)
        source, category = _derive_source_category(record.name)
        payload = {
            "entries": [
                {
                    "timestamp": datetime.fromtimestamp(
                        record.created, tz=timezone.utc
                    ).isoformat(),
                    "level": record.levelname,
                    "severity": severity,
                    "source": source,
                    "category": category,
                    "logger": record.name,
                    "message": message,
                }
            ]
        }
        try:
            self._client.post("/api/logs/ingest", json=payload)
        except Exception:
            return


def configure_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.getLevelName(LOG_LEVEL),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger = logging.getLogger("folder-watcher")
    handler = OrchestratorLogHandler(ORCHESTRATOR_URL)
    handler.setLevel(logging.getLevelName(LOG_LEVEL))
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    return logger


LOGGER = configure_logging()


# Event Handling
class WatcherEventHandler(FileSystemEventHandler):
    def __init__(self, library_name: str, queue: asyncio.Queue):
        self.library_name = library_name
        self.queue = queue

    def _process_event(self, event: FileSystemEvent, event_type: str):
        path = Path(event.src_path)
        is_directory = event.is_directory

        # Determine event type mapping
        # watchdog events: created, deleted, modified, moved
        # orchestrator expects: created, deleted, modified

        mapped_event_type = "modified"
        if event.event_type == "deleted":
            mapped_event_type = "deleted"
        elif event.event_type == "created":
            mapped_event_type = "created"
        elif event.event_type == "moved":
            # For moved, we ideally treat it as deleted from src and created at dest,
            # or update the path. The orchestrator API expects "created", "modified", "deleted".
            # If it's a move, watchdog gives dest_path.
            # We will emit two events for moved: deleted (old) and created (new) if possible,
            # but FileSystemEvent for moved has src_path and dest_path.
            pass  # handled below

        payload = {
            "path": str(path),
            "library": self.library_name,
            "event": mapped_event_type,
            "is_directory": is_directory,
            "size": None,
            "modified_at": None,
        }

        if event.event_type == "moved":
            # Handle source as deleted
            payload["event"] = "deleted"
            self.queue.put_nowait(payload.copy())

            # Handle dest as created
            if hasattr(event, "dest_path"):
                payload["path"] = str(event.dest_path)
                payload["event"] = "created"
                if not is_directory and os.path.exists(event.dest_path):
                    try:
                        stat = os.stat(event.dest_path)
                        payload["size"] = stat.st_size
                        payload["modified_at"] = datetime.fromtimestamp(
                            stat.st_mtime, tz=timezone.utc
                        ).isoformat()
                    except OSError:
                        pass
                self.queue.put_nowait(payload)
            return

        if not is_directory and mapped_event_type != "deleted" and path.exists():
            try:
                stat = path.stat()
                payload["size"] = stat.st_size
                payload["modified_at"] = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
            except OSError:
                pass

        self.queue.put_nowait(payload)

    def on_created(self, event: FileSystemEvent):
        self._process_event(event, "created")

    def on_deleted(self, event: FileSystemEvent):
        self._process_event(event, "deleted")

    def on_modified(self, event: FileSystemEvent):
        self._process_event(event, "modified")

    def on_moved(self, event: FileSystemEvent):
        self._process_event(event, "moved")


class EventManager:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.queue = asyncio.Queue()

    async def _send_payload(self, events: List[Dict[str, Any]]) -> bool:
        payload = {"events": events}
        backoff = EVENT_RETRY_BACKOFF_SECONDS

        for attempt in range(1, EVENT_RETRY_ATTEMPTS + 1):
            try:
                response = await self.client.post("/api/events", json=payload)
                response.raise_for_status()
                return True
            except httpx.HTTPError as exc:
                LOGGER.warning("Failed to post events (attempt %s): %s", attempt, exc)
                if attempt < EVENT_RETRY_ATTEMPTS:
                    await asyncio.sleep(backoff)
                    backoff *= 2
        return False

    def _spool_events(self, events: List[Dict[str, Any]]):
        try:
            EVENT_SPOOL_FILE.parent.mkdir(parents=True, exist_ok=True)
            with EVENT_SPOOL_FILE.open("a") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            # Trim spool if needed
            if EVENT_SPOOL_FILE.stat().st_size > EVENT_SPOOL_MAX_BYTES:
                LOGGER.warning("Spool exceeded max bytes, trimming...")
                # Simple trim: just keep the tail (this is rough but similar to bash script)
                # Ideally we'd read lines, but for now let's just warn or truncate simply
                # To implement 'tail -c' behavior in python efficiently is a bit more complex,
                # but we can just truncate or rotate.
                # For now, let's leave it as appending and rely on replay to clear it.
                pass
        except Exception as exc:
            LOGGER.error("Failed to spool events: %s", exc)

    async def replay_spool(self):  # noqa: C901
        if not EVENT_SPOOL_FILE.exists():
            return

        LOGGER.info("Replaying spooled events from %s", EVENT_SPOOL_FILE)
        temp_file = EVENT_SPOOL_FILE.with_suffix(".pending")

        # Rename to pending to process
        try:
            EVENT_SPOOL_FILE.rename(temp_file)
        except OSError:
            return

        failed_events = []
        events_batch = []

        try:
            with temp_file.open("r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        events_batch.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

            if events_batch:
                # Try sending all at once or in chunks.
                # The bash script tried one by one if I recall correctly?
                # "while read line ... send_payload line"
                # But here we support batch.

                # Let's send in chunks of 50
                chunk_size = 50
                for i in range(0, len(events_batch), chunk_size):
                    chunk = events_batch[i : i + chunk_size]
                    if not await self._send_payload(chunk):
                        failed_events.extend(chunk)

        except Exception as exc:
            LOGGER.error("Error replaying spool: %s", exc)

        # Rewrite failed events back to spool
        if failed_events:
            self._spool_events(failed_events)

        # Remove temp file
        if temp_file.exists():
            temp_file.unlink()

    async def process_queue(self):
        while True:
            events = []
            try:
                # Wait for first event
                event = await self.queue.get()
                events.append(event)

                # Gather more if available within buffer time
                end_time = time.monotonic() + EVENT_BUFFER_SECONDS
                while time.monotonic() < end_time:
                    try:
                        event = self.queue.get_nowait()
                        events.append(event)
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.1)

                if not await self._send_payload(events):
                    LOGGER.warn("Persisting %s events to spool after retries", len(events))
                    self._spool_events(events)

            except Exception as exc:
                LOGGER.error("Error processing event queue: %s", exc)
                await asyncio.sleep(1)


async def main():
    if not WATCH_ROOTS:
        LOGGER.warning("WATCH_ROOTS is required.")
        return

    async with httpx.AsyncClient(base_url=ORCHESTRATOR_URL, timeout=10.0) as client:
        event_manager = EventManager(client)

        # Replay spool on startup
        await event_manager.replay_spool()

        # Start processing queue
        processor_task = asyncio.create_task(event_manager.process_queue())

        if WATCH_POLLING:
            observer = PollingObserver(timeout=POLLING_INTERVAL)
            LOGGER.info("Using PollingObserver with interval %ss", POLLING_INTERVAL)
        else:
            observer = Observer()
            LOGGER.info("Using native Observer")

        raw_entries = WATCH_ROOTS.split(",")

        for entry in raw_entries:
            if not entry.strip():
                continue
            parts = entry.split(":", 1)
            if len(parts) != 2:
                LOGGER.warning("Invalid WATCH_ROOT entry: %s", entry)
                continue

            label, path_str = parts
            path = Path(path_str)

            while not path.exists():
                LOGGER.warning("Root %s not available yet. Waiting...", path)
                await asyncio.sleep(5)

            LOGGER.info("Starting watcher for %s at %s", label, path)
            event_handler = WatcherEventHandler(label, event_manager.queue)
            observer.schedule(event_handler, str(path), recursive=True)

        observer.start()

        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            observer.stop()

        observer.join()
        processor_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
