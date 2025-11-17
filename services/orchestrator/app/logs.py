from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Iterable, List, Optional, Set


@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }


class InMemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._buffer: Deque[LogEntry] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        entry = LogEntry(
            timestamp=datetime.fromtimestamp(record.created),
            level=record.levelname,
            logger=record.name,
            message=message,
        )
        self._buffer.append(entry)

    def _filter_entries(
        self,
        *,
        level: Optional[str] = None,
        query: Optional[str] = None,
        logger_name: Optional[str] = None,
    ) -> Iterable[LogEntry]:
        for entry in reversed(self._buffer):
            if level and entry.level.lower() != level.lower():
                continue
            if logger_name and entry.logger.lower() != logger_name.lower():
                continue
            if query and query.lower() not in entry.message.lower():
                continue
            yield entry

    def list_entries(
        self,
        *,
        level: Optional[str] = None,
        query: Optional[str] = None,
        logger_name: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        filtered = self._filter_entries(level=level, query=query, logger_name=logger_name)
        results = []
        for entry in filtered:
            results.append(entry.to_dict())
            if len(results) >= limit:
                break
        return results

    def list_categories(self) -> List[str]:
        seen: Set[str] = set()
        for entry in self._buffer:
            seen.add(entry.logger)
        return sorted(seen)
