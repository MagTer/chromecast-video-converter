from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import List, Optional

from .context import get_request_id

VERBOSE_LEVEL_NAME = "VERBOSE"
logging.addLevelName(logging.DEBUG, VERBOSE_LEVEL_NAME)


def derive_source_category(logger_name: str) -> tuple[str, str]:
    normalized = logger_name or "orchestrator"
    parts = normalized.replace(" ", "_").split(".")
    source = parts[0] if parts else normalized
    category = ".".join(parts[1:]) if len(parts) > 1 else normalized
    return source, category or source


def _normalize_level(level: str) -> str:
    normalized = level.upper()
    if normalized == "DEBUG":
        return VERBOSE_LEVEL_NAME
    return normalized


def _severity_value(level: str) -> int:
    normalized = _normalize_level(level)
    order = {
        VERBOSE_LEVEL_NAME: 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
    }
    return order.get(normalized, 0)


def severity_value(level: str) -> int:
    return _severity_value(level)


def _ensure_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    severity: str
    severity_value: int
    source: str
    category: str
    logger: str
    message: str
    request_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "timestamp": _ensure_utc(self.timestamp).isoformat(),
            "level": _normalize_level(self.level),
            "severity": _normalize_level(self.severity),
            "severity_value": self.severity_value,
            "source": self.source,
            "category": self.category,
            "logger": self.logger,
            "message": self.message,
            "request_id": self.request_id,
        }


class LogStore:
    def __init__(self, path: Path, retention_days: int = 7) -> None:
        self.path = path
        self.retention_days = retention_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    level TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    severity_value INTEGER NOT NULL,
                    logger TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    request_id TEXT
                )
                """
            )
            self._ensure_columns()
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(severity_value)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_logger ON logs(category)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_request_id ON logs(request_id)")
            self._conn.commit()
            self._prune_expired()

    def _ensure_columns(self) -> None:
        with self._lock:
            columns = {
                row["name"] for row in self._conn.execute("PRAGMA table_info(logs)").fetchall()
            }
            schema_updated = False
            if "severity" not in columns:
                self._conn.execute("ALTER TABLE logs ADD COLUMN severity TEXT NOT NULL DEFAULT ''")
                schema_updated = True
            if "severity_value" not in columns:
                self._conn.execute(
                    "ALTER TABLE logs ADD COLUMN severity_value INTEGER NOT NULL DEFAULT 0"
                )
                schema_updated = True
            if "source" not in columns:
                self._conn.execute("ALTER TABLE logs ADD COLUMN source TEXT NOT NULL DEFAULT ''")
                schema_updated = True
            if "category" not in columns:
                self._conn.execute("ALTER TABLE logs ADD COLUMN category TEXT NOT NULL DEFAULT ''")
                schema_updated = True
            if "request_id" not in columns:
                self._conn.execute("ALTER TABLE logs ADD COLUMN request_id TEXT")
                schema_updated = True
            if schema_updated:
                self._conn.commit()
            self._backfill_schema()

    def _backfill_schema(self) -> None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, level, logger, severity, severity_value, source, category FROM logs"
            )
            rows = cursor.fetchall()
            for row in rows:
                severity = row["severity"] or row["level"]
                source = row["source"]
                category = row["category"]
                if not source or not category:
                    derived_source, derived_category = derive_source_category(row["logger"])
                    source = source or derived_source
                    category = category or derived_category
                normalized_severity = _normalize_level(severity)
                self._conn.execute(
                    """
                    UPDATE logs
                    SET severity = ?, severity_value = ?, source = ?, category = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_severity,
                        _severity_value(normalized_severity),
                        source,
                        category,
                        row["id"],
                    ),
                )
            if rows:
                self._conn.commit()

    def _prune_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        with self._lock:
            self._conn.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff.timestamp(),))
            self._conn.commit()

    def update_retention(self, retention_days: int) -> None:
        self.retention_days = retention_days
        self._prune_expired()

    def add_entry(self, entry: LogEntry) -> None:
        utc_timestamp = _ensure_utc(entry.timestamp)
        normalized_severity = _normalize_level(entry.severity or entry.level)
        severity_value = _severity_value(normalized_severity)
        source = entry.source
        category = entry.category
        if not source or not category:
            source, category = derive_source_category(entry.logger)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO logs(
                    timestamp,
                    level,
                    severity,
                    severity_value,
                    logger,
                    source,
                    category,
                    message,
                    request_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_timestamp.timestamp(),
                    entry.level,
                    normalized_severity,
                    severity_value,
                    entry.logger,
                    source,
                    category,
                    entry.message,
                    entry.request_id,
                ),
            )
            self._conn.commit()
        self._prune_expired()

    def _filter_query(
        self,
        *,
        level: Optional[str] = None,
        severity: Optional[str] = None,
        min_severity: Optional[str] = None,
        query: Optional[str] = None,
        logger_name: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> tuple[str, list]:
        sql = (
            "SELECT timestamp, level, severity, severity_value, logger, source, "
            "category, message, request_id FROM logs"
        )
        clauses = []
        params: list = []
        minimum = min_severity or severity or level
        if minimum:
            params.append(_severity_value(minimum))
            clauses.append("severity_value >= ?")
        if category:
            clauses.append("LOWER(category) = ?")
            params.append(category.lower())
        if logger_name:
            clauses.append("LOWER(logger) = ?")
            params.append(logger_name.lower())
        if source:
            clauses.append("LOWER(source) = ?")
            params.append(source.lower())
        if request_id:
            clauses.append("request_id = ?")
            params.append(request_id)
        if query:
            clauses.append("LOWER(message) LIKE ?")
            params.append(f"%{query.lower()}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(500)
        return sql, params

    def list_entries(
        self,
        *,
        level: Optional[str] = None,
        severity: Optional[str] = None,
        min_severity: Optional[str] = None,
        query: Optional[str] = None,
        logger_name: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        request_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        sql, params = self._filter_query(
            level=level,
            severity=severity,
            min_severity=min_severity,
            query=query,
            logger_name=logger_name,
            category=category,
            source=source,
            request_id=request_id,
        )
        params[-1] = limit
        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
        entries = []
        for row in rows:
            row_keys = set(row.keys())
            severity = row["severity"] if "severity" in row_keys else row["level"]
            category = row["category"] if "category" in row_keys else row["logger"]
            source = row["source"] if "source" in row_keys else ""
            req_id = row["request_id"] if "request_id" in row_keys else None
            entries.append(
                LogEntry(
                    timestamp=_ensure_utc(
                        datetime.fromtimestamp(row["timestamp"], tz=timezone.utc)
                    ),
                    level=row["level"],
                    severity=severity,
                    severity_value=(
                        row["severity_value"]
                        if "severity_value" in row_keys
                        else _severity_value(severity)
                    ),
                    logger=row["logger"],
                    source=source,
                    category=category,
                    message=row["message"],
                    request_id=req_id,
                ).to_dict()
            )
        return entries

    def list_categories(self) -> List[str]:
        with self._lock:
            cursor = self._conn.execute("SELECT DISTINCT category FROM logs ORDER BY category")
            rows = cursor.fetchall()
        return [row[0] for row in rows]

    def list_sources(self) -> List[str]:
        with self._lock:
            cursor = self._conn.execute("SELECT DISTINCT source FROM logs ORDER BY source")
            rows = cursor.fetchall()
        return [row[0] for row in rows]

    def stats(self) -> dict:
        size_bytes = self.path.stat().st_size if self.path.exists() else 0
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM logs")
            total_entries = cursor.fetchone()[0]
        return {
            "retention_days": self.retention_days,
            "file_size_bytes": size_bytes,
            "total_entries": total_entries,
        }


class StructuredLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        """Attach structured context to log records before formatting."""

        derived_source, derived_category = derive_source_category(record.name)
        severity = getattr(record, "severity", record.levelname)
        normalized_severity = _normalize_level(severity)
        record.severity = normalized_severity
        record.severity_value = getattr(
            record, "severity_value", _severity_value(normalized_severity)
        )
        record.source = getattr(record, "source", None) or derived_source
        record.category = getattr(record, "category", None) or derived_category
        record.request_id = getattr(record, "request_id", None) or get_request_id()
        return True


class SQLiteLogHandler(logging.Handler):
    def __init__(self, store: LogStore) -> None:
        super().__init__()
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        source = getattr(record, "source", None)
        category = getattr(record, "category", None)
        severity = getattr(record, "severity", record.levelname)
        severity_value = getattr(record, "severity_value", _severity_value(severity))
        derived_source, derived_category = derive_source_category(record.name)
        request_id = getattr(record, "request_id", None)
        entry = LogEntry(
            timestamp=_ensure_utc(datetime.fromtimestamp(record.created, tz=timezone.utc)),
            level=record.levelname,
            severity=severity,
            severity_value=severity_value,
            logger=record.name,
            source=source or derived_source,
            category=category or derived_category,
            message=message,
            request_id=request_id,
        )
        try:
            self.store.add_entry(entry)
        except Exception:  # noqa: BLE001
            # Avoid breaking the running service if the log store is temporarily unavailable.
            self.handleError(record)
