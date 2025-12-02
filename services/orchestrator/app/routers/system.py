from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ..dependencies import (
    INDEX_HTML,
    NOTIFIER,
    WORKER_TELEMETRY,
    get_app_dependencies,
    get_library_map,
    worker_metrics_summary,
)
from ..schemas import WorkerTelemetryPayload

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@router.get("/api/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "libraries": len(get_library_map())})


@router.get("/api/readyz")
async def readyz() -> JSONResponse:
    return JSONResponse({"status": "ready"})


@router.websocket("/ws")
async def websocket_updates(websocket: WebSocket) -> None:
    await NOTIFIER.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await NOTIFIER.disconnect(websocket)


@router.post("/api/workers/telemetry")
async def ingest_worker_telemetry(payload: WorkerTelemetryPayload) -> JSONResponse:
    WORKER_TELEMETRY[payload.worker_id] = payload
    return JSONResponse({"stored": True})


@router.get("/api/metrics")
async def metrics() -> JSONResponse:
    jobs_list = await get_app_dependencies().job_manager.list_jobs()
    count_by_status: Dict[str, int] = {}
    for job in jobs_list:
        count_by_status[job.status] = count_by_status.get(job.status, 0) + 1
    worker_metrics = worker_metrics_summary()
    return JSONResponse({"jobs": count_by_status, "workers": worker_metrics})