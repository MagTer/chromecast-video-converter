from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..dependencies import get_app_dependencies
from ..logs import LogEntry, derive_source_category, severity_value
from ..schemas import LogIngestBatch

router = APIRouter()


@router.get("/api/logs")
async def list_logs(
    level: Optional[str] = None,
    min_severity: Optional[str] = None,
    query: Optional[str] = None,
    logger: Optional[str] = None,
    source: Optional[str] = None,
    category: Optional[str] = None,
    request_id: Optional[str] = None,
    before: Optional[datetime] = None,
    limit: int = 200,
) -> JSONResponse:
    severity_filter = None if min_severity == "ALL" else (min_severity or "INFO")
    entries = get_app_dependencies().log_store.list_entries(
        level=level,
        min_severity=severity_filter,
        query=query,
        logger_name=logger,
        category=category,
        source=source,
        request_id=request_id,
        before=before,
        limit=max(1, min(limit, 1000)),
    )
    return JSONResponse(entries)


@router.get("/api/logs/categories")
async def list_log_categories() -> JSONResponse:
    return JSONResponse(get_app_dependencies().log_store.list_categories())


@router.get("/api/logs/sources")
async def list_log_sources() -> JSONResponse:
    return JSONResponse(get_app_dependencies().log_store.list_sources())


@router.get("/api/logs/stats")
async def log_stats() -> JSONResponse:
    return JSONResponse(get_app_dependencies().log_store.stats())


@router.post("/api/logs/ingest")
async def ingest_logs(batch: LogIngestBatch) -> JSONResponse:
    entries = []
    for entry in batch.entries:
        severity = entry.severity or entry.level
        source = entry.source
        category = entry.category
        if not source or not category:
            derived_source, derived_category = derive_source_category(entry.logger)
            source = source or derived_source
            category = category or derived_category
        entries.append(
            LogEntry(
                timestamp=entry.timestamp or datetime.now(timezone.utc),
                level=entry.level,
                severity=severity,
                severity_value=severity_value(severity),
                logger=entry.logger,
                source=source,
                category=category,
                message=entry.message,
                request_id=entry.request_id,
            )
        )
    # SQLite writes block; keep them off the event loop and in one transaction.
    stored = await run_in_threadpool(get_app_dependencies().log_store.add_entries, entries)
    return JSONResponse({"stored": stored})
