from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import List, Optional

VERBOSE_LEVEL_NAME = "VERBOSE"
logging.addLevelName(logging.DEBUG, VERBOSE_LEVEL_NAME)


def _normalize_level(level: str) -> str:
    normalized = level.upper()
    if normalized == "DEBUG":
        return VERBOSE_LEVEL_NAME
    return normalized


def _ensure_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict:
        return {
            "timestamp": _ensure_utc(self.timestamp).isoformat(),
            "level": _normalize_level(self.level),
            "logger": self.logger,
            "message": self.message,
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
                    logger TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_logger ON logs(logger)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
            self._conn.commit()
            self._prune_expired()

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
        normalized_level = _normalize_level(entry.level)
        with self._lock:
            self._conn.execute(
                "INSERT INTO logs(timestamp, level, logger, message) VALUES (?, ?, ?, ?)",
                (utc_timestamp.timestamp(), normalized_level, entry.logger, entry.message),
            )
            self._conn.commit()
        self._prune_expired()

    def _filter_query(
        self,
        *,
        level: Optional[str] = None,
        query: Optional[str] = None,
        logger_name: Optional[str] = None,
    ) -> tuple[str, list]:
        sql = "SELECT timestamp, level, logger, message FROM logs"
        clauses = []
        params: list = []
        if level:
            normalized = _normalize_level(level)
            if normalized == VERBOSE_LEVEL_NAME:
                clauses.append("(level = ? OR level = ?)")
                params.extend([VERBOSE_LEVEL_NAME, "DEBUG"])
            else:
                clauses.append("level = ?")
                params.append(normalized)
        if logger_name:
            clauses.append("LOWER(logger) = ?")
            params.append(logger_name.lower())
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
        query: Optional[str] = None,
        logger_name: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        sql, params = self._filter_query(level=level, query=query, logger_name=logger_name)
        params[-1] = limit
        with self._lock:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
        entries = []
        for row in rows:
            entries.append(
                LogEntry(
                    timestamp=_ensure_utc(
                        datetime.fromtimestamp(row["timestamp"], tz=timezone.utc)
                    ),
                    level=_normalize_level(row["level"]),
                    logger=row["logger"],
                    message=row["message"],
                ).to_dict()
            )
        return entries

    def list_categories(self) -> List[str]:
        with self._lock:
            cursor = self._conn.execute("SELECT DISTINCT logger FROM logs ORDER BY logger")
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


class SQLiteLogHandler(logging.Handler):
    def __init__(self, store: LogStore) -> None:
        super().__init__()
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        entry = LogEntry(
            timestamp=_ensure_utc(datetime.fromtimestamp(record.created, tz=timezone.utc)),
            level=_normalize_level(record.levelname),
            logger=record.name,
            message=message,
        )
        try:
            self.store.add_entry(entry)
        except Exception:  # noqa: BLE001
            # Avoid breaking the running service if the log store is temporarily unavailable.
            self.handleError(record)
