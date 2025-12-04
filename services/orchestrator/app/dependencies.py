import asyncio
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Set, Tuple

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
def _resolve_data_dir(data_dir: Path | None = None) -> Path:
    if data_dir:
        resolved_path = data_dir.resolve()
    else:
        resolved_path = Path(os.environ.get("DATA_DIR", "/app/data")).resolve()
    try:
        resolved_path.mkdir(parents=True, exist_ok=True)
        return resolved_path
    except OSError:
        fallback = Path("./data").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _get_log_db_path(data_dir: Path | None = None) -> Path:
    return Path(os.environ.get("LOG_DB_PATH", _resolve_data_dir(data_dir) / "events.db")).resolve()


def _get_config_db_path(data_dir: Path | None = None) -> Path:
    return Path(
        os.environ.get("CONFIG_DB_PATH", _resolve_data_dir(data_dir) / "config.db")
    ).resolve()


def _get_library_db_path(data_dir: Path | None = None) -> Path:
    return Path(
        os.environ.get("LIBRARY_DB_PATH", _resolve_data_dir(data_dir) / "library.db")
    ).resolve()


JOB_QUEUE_URL = os.environ.get("JOB_QUEUE", "redis://localhost:6379/0")
JOB_VISIBILITY_TIMEOUT = int(os.environ.get("JOB_VISIBILITY_TIMEOUT", "300"))


class AppDependencies:
    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir

    @property
    @lru_cache
    def data_dir(self) -> Path:
        return _resolve_data_dir(self._data_dir)

    @property
    @lru_cache
    def log_store(self) -> LogStore:
        return LogStore(_get_log_db_path(self._data_dir))

    @property
    @lru_cache
    def session_factory_and_engine(self) -> Tuple[Any, Any]:
        session_factory, engine = create_session_factory(_get_library_db_path(self._data_dir))
        Base.metadata.create_all(engine)
        return session_factory, engine

    @property
    @lru_cache
    def session_factory(self) -> Any:
        return self.session_factory_and_engine[0]

    @property
    @lru_cache
    def engine(self) -> Any:
        return self.session_factory_and_engine[1]

    @property
    @lru_cache
    def profile_store(self) -> ProfileStore:
        return ProfileStore(self.session_factory)

    @property
    @lru_cache
    def library_config_store(self) -> LibraryConfigStore:
        return LibraryConfigStore(self.session_factory)

    @property
    @lru_cache
    def library_entry_store(self) -> LibraryEntryStore:
        return LibraryEntryStore(
            _get_library_db_path(self._data_dir),
            session_factory=self.session_factory,
            engine=self.engine,
        )

    @property
    @lru_cache
    def job_history_store(self) -> JobHistoryStore:
        return JobHistoryStore(self.session_factory)

    @property
    @lru_cache
    def config_service(self) -> config_module.ConfigService:
        return config_module.ConfigService(_get_config_db_path(self._data_dir))

    @property
    @lru_cache
    def job_manager(self) -> jobs.JobManager:
        return jobs.JobManager(JOB_QUEUE_URL, visibility_timeout=JOB_VISIBILITY_TIMEOUT)


_global_app_dependencies: AppDependencies | None = None


def get_app_dependencies() -> AppDependencies:
    global _global_app_dependencies
    if _global_app_dependencies is None:
        _global_app_dependencies = AppDependencies()
    return _global_app_dependencies


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
    return {
        library.name: library
        for library in get_app_dependencies().library_config_store.list_libraries()
    }


HOST_ENVIRONMENT = {"is_wsl2": detect_wsl2()}
