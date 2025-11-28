import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Set

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

from . import config as config_module
from . import jobs
from .db import Base, create_session_factory
from .job_history import JobHistoryStore
from .library_entries import LibraryEntryStore
from .logs import LogStore
from .profiles import LibraryConfigStore, ProfileStore
from .schemas import WorkerTelemetryPayload
from .utils import (
    detect_wsl2,
)


# Environment Variables
def resolve_data_dir() -> Path:
    default = Path(os.environ.get("DATA_DIR", "/app/data")).resolve()
    try:
        default.mkdir(parents=True, exist_ok=True)
        return default
    except OSError:
        fallback = Path("./data").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR = resolve_data_dir()

LOG_DB_PATH = Path(os.environ.get("LOG_DB_PATH", DATA_DIR / "events.db")).resolve()
CONFIG_DB_PATH = Path(os.environ.get("CONFIG_DB_PATH", DATA_DIR / "config.db")).resolve()
CONFIG_TEMPLATE_PATH = Path(
    os.environ.get("CONFIG_TEMPLATE_PATH", "/app/config/settings.yaml.template")
).resolve()
LEGACY_CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config/settings.yaml")).resolve()
LIBRARY_DB_PATH = Path(os.environ.get("LIBRARY_DB_PATH", DATA_DIR / "library.db")).resolve()
JOB_QUEUE_URL = os.environ.get("JOB_QUEUE", "redis://localhost:6379/0")
JOB_VISIBILITY_TIMEOUT = int(os.environ.get("JOB_VISIBILITY_TIMEOUT", "300"))

# Database & Stores
LOG_STORE = LogStore(LOG_DB_PATH)
SESSION_FACTORY, ENGINE = create_session_factory(LIBRARY_DB_PATH)

# Ensure tables exist
Base.metadata.create_all(ENGINE)

PROFILE_STORE = ProfileStore(SESSION_FACTORY)
LIBRARY_CONFIG_STORE = LibraryConfigStore(SESSION_FACTORY)
LIBRARY_STORE = LibraryEntryStore(LIBRARY_DB_PATH, session_factory=SESSION_FACTORY, engine=ENGINE)
JOB_HISTORY_STORE = JobHistoryStore(SESSION_FACTORY)

# Services
config_service = config_module.ConfigService(
    CONFIG_DB_PATH, CONFIG_TEMPLATE_PATH, LEGACY_CONFIG_PATH
)
job_manager = jobs.JobManager(JOB_QUEUE_URL, visibility_timeout=JOB_VISIBILITY_TIMEOUT)

# Global State
WORKER_TELEMETRY: Dict[str, WorkerTelemetryPayload] = {}


# Websocket
class WebsocketNotifier:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        payload = jsonable_encoder(message)
        async with self._lock:
            targets = list(self._connections)
        if not targets:
            return

        async def _send(ws: WebSocket) -> None:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect(ws)

        await asyncio.gather(*(_send(ws) for ws in targets), return_exceptions=True)


NOTIFIER = WebsocketNotifier()


def worker_metrics_summary() -> Dict[str, Any]:
    payloads = list(WORKER_TELEMETRY.values())
    telemetry = [jsonable_encoder(item) for item in payloads]
    available = sum(1 for item in payloads if item.gpu_available)
    return {"workers": len(payloads), "available": available, "telemetry": telemetry}


BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"
try:
    INDEX_HTML = TEMPLATE_PATH.read_text()
except FileNotFoundError:
    INDEX_HTML = "<h1>Dashboard Placeholder</h1>"


def get_library_map() -> Dict[str, Any]:
    return {library.name: library for library in LIBRARY_CONFIG_STORE.list_libraries()}


HOST_ENVIRONMENT = {"is_wsl2": detect_wsl2()}
print(f"DEBUG: LIBRARY_DB_PATH resolved to {LIBRARY_DB_PATH}")
